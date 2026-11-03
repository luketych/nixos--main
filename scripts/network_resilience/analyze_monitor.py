"""Read-only analysis of a network-resilience monitor run directory."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import statistics
import sys

MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_TIMELINE = 20
VALID_STATUSES = {"running", "completed", "interrupted", "stopped-identity-or-authentication-error"}


def _timestamp(value):
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc) if parsed.tzinfo else None
    except ValueError:
        return None


def _counter(summary, key, anomalies):
    value = summary.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        anomalies.append(f"summary has invalid {key}")
        return None
    return value


def analyze(run_directory, now=None):
    """Return a JSON-serializable report; source files are opened read-only."""
    directory = Path(run_directory)
    anomalies = []
    summary = None
    summary_path = directory / "summary.json"
    try:
        if summary_path.stat().st_size > MAX_FILE_BYTES:
            anomalies.append(f"summary.json exceeds {MAX_FILE_BYTES} byte safety limit")
        else:
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            if not isinstance(summary, dict):
                anomalies.append("summary.json must contain an object")
                summary = None
    except FileNotFoundError:
        anomalies.append("missing summary.json")
    except (OSError, UnicodeError, json.JSONDecodeError):
        anomalies.append("summary.json is unreadable or invalid JSON")

    events, source_events_valid = [], True
    path = directory / "events.jsonl"
    try:
        if path.stat().st_size > MAX_FILE_BYTES:
            anomalies.append(f"events.jsonl exceeds {MAX_FILE_BYTES} byte safety limit")
            source_events_valid = False
        else:
            raw = path.read_bytes()
            if raw and not raw.endswith(b"\n"):
                anomalies.append("trailing unterminated event fragment ignored")
                source_events_valid = False
                raw = raw[:raw.rfind(b"\n") + 1] if b"\n" in raw else b""
            for number, line in enumerate(raw.splitlines(), 1):
                try:
                    event = json.loads(line)
                    if not isinstance(event, dict):
                        raise ValueError
                    kind = event.get("type")
                    if kind not in ("ssh_attempt", "sampling_gap"):
                        anomalies.append(f"line {number}: unknown event type")
                        source_events_valid = False
                        continue
                    stamp = _timestamp(event.get("timestamp_utc"))
                    if stamp is None:
                        anomalies.append(f"line {number}: invalid timestamp")
                        source_events_valid = False
                        continue
                    event["_parsed_timestamp"] = stamp
                    valid = True
                    if kind == "ssh_attempt":
                        if not isinstance(event.get("result"), str) or not event["result"]:
                            anomalies.append(f"line {number}: invalid result")
                            source_events_valid = False
                            continue
                        for field in ("elapsed_seconds", "lateness_seconds"):
                            value = event.get(field)
                            if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value) or value < 0:
                                anomalies.append(f"line {number}: invalid {field}")
                                valid = False
                        if _timestamp(event.get("scheduled_utc")) is None:
                            anomalies.append(f"line {number}: invalid scheduled_utc")
                            valid = False
                    else:
                        missed = event.get("missed_slots")
                        if not isinstance(missed, int) or isinstance(missed, bool) or missed < 0:
                            anomalies.append(f"line {number}: invalid missed_slots")
                            valid = False
                    if not valid:
                        source_events_valid = False
                        continue
                    events.append(event)
                except (json.JSONDecodeError, ValueError):
                    anomalies.append(f"line {number}: malformed complete event line")
                    source_events_valid = False
    except FileNotFoundError:
        anomalies.append("missing events.jsonl")
        source_events_valid = False
    except OSError:
        anomalies.append("events.jsonl is unreadable")
        source_events_valid = False

    attempts = [e for e in events if e["type"] == "ssh_attempt"]
    gaps = [e for e in events if e["type"] == "sampling_gap"]
    successes = sum(e["result"] == "success" for e in attempts)
    failures = len(attempts) - successes
    categories = {}
    for event in attempts:
        if event["result"] != "success":
            categories[event["result"]] = categories.get(event["result"], 0) + 1
    durations = sorted(e["elapsed_seconds"] for e in attempts if e.get("result") == "success" and isinstance(e.get("elapsed_seconds"), (int, float)) and not isinstance(e.get("elapsed_seconds"), bool) and math.isfinite(e["elapsed_seconds"]) and e["elapsed_seconds"] >= 0)
    latency = {}
    if durations:
        pos = (len(durations) - 1) * .95
        low = int(pos)
        latency = {"median": statistics.median(durations), "p95": durations[low] + (durations[min(low + 1, len(durations) - 1)] - durations[low]) * (pos - low)}

    status = summary.get("status", "unknown") if summary else "unknown"
    if not isinstance(status, str):
        anomalies.append("invalid status")
        status = "unknown"
    if summary:
        if type(summary.get("schema_version")) is not int or summary.get("schema_version") != 1:
            anomalies.append("unsupported or invalid schema_version")
        if status not in VALID_STATUSES:
            anomalies.append("invalid status")
    started = _timestamp(summary.get("started_utc")) if summary else None
    scheduled_end = _timestamp(summary.get("scheduled_end_utc")) if summary else None
    completed_at = _timestamp(summary.get("completed_utc")) if summary else None
    if summary:
        for key, value in (("started_utc", started), ("scheduled_end_utc", scheduled_end)):
            if value is None:
                anomalies.append(f"invalid {key}")
        if status == "completed" and completed_at is None:
            anomalies.append("invalid completed_utc")
    interval = _counter(summary, "interval_seconds", anomalies) if summary else None
    duration = _counter(summary, "duration_seconds", anomalies) if summary else None
    expected_attempts = _counter(summary, "expected_attempts", anomalies) if summary else None
    counter_values = {key: _counter(summary, key, anomalies) for key in ("attempts", "successes", "failures", "skipped_slots")} if summary else {}
    if interval is not None and duration is not None and expected_attempts is not None:
        if interval <= 0 or duration <= 0 or duration % interval or duration // interval != expected_attempts:
            anomalies.append("expected_attempts is inconsistent with interval_seconds and duration_seconds")
    if started and scheduled_end and scheduled_end <= started:
        anomalies.append("scheduled window is not ordered")
    if started and scheduled_end and duration is not None and (scheduled_end - started).total_seconds() != duration:
        anomalies.append("scheduled window length differs from duration_seconds")
    if status == "completed" and completed_at and scheduled_end and completed_at < scheduled_end:
        anomalies.append("completed_utc precedes scheduled_end_utc")

    skipped = sum(e["missed_slots"] for e in gaps)
    observed_counters = {"attempts": len(attempts), "successes": successes, "failures": failures, "skipped_slots": skipped}
    mismatch = any(counter_values.get(k) is not None and counter_values[k] != v for k, v in observed_counters.items())
    if mismatch:
        anomalies.append("summary counters differ from events; possible concurrent snapshot race" if status == "running" else "summary counters differ from events; completed data is inconsistent")
    accounted = len(attempts) + skipped
    if expected_attempts is not None and (accounted > expected_attempts or
                                         (status == "completed" and accounted != expected_attempts)):
        anomalies.append("observed attempts plus skipped slots do not equal expected_attempts")

    if started and scheduled_end and interval and interval > 0 and expected_attempts is not None:
        # Reconstruct the monitor's append-order slot accounting. A skipped-slot
        # count alone cannot certify a window if the gap is outside that window,
        # overlaps an attempt, or claims slots inconsistent with its timestamp.
        next_slot = 0
        seen_slots = set()
        for event in events:
            stamp = event["_parsed_timestamp"]
            if stamp < started or (status == "completed" and completed_at and stamp > completed_at):
                anomalies.append("event timestamp outside observed run")
                break
            if event["type"] == "sampling_gap":
                reason = event.get("reason")
                if reason == "scheduler-late-or-sleep" and stamp < scheduled_end:
                    current_slot = min(int((stamp - started).total_seconds() // interval), expected_attempts - 1)
                    if event["missed_slots"] <= 0 or current_slot - next_slot != event["missed_slots"]:
                        anomalies.append("sampling gap count does not match its timestamp/cadence")
                        break
                    next_slot = current_slot
                elif reason == "window-elapsed-before-slot" and stamp >= scheduled_end:
                    if event["missed_slots"] <= 0 or event["missed_slots"] != expected_attempts - next_slot:
                        anomalies.append("end-of-window sampling gap count is inconsistent")
                        break
                    next_slot = expected_attempts
                else:
                    anomalies.append("sampling gap timestamp or reason outside monitoring window")
                    break
                continue
            scheduled = _timestamp(event.get("scheduled_utc"))
            if scheduled is None:
                continue
            delta = (scheduled - started).total_seconds()
            slot = round(delta / interval)
            if delta < 0 or scheduled >= scheduled_end or abs(delta - slot * interval) > 0.001:
                anomalies.append("attempt scheduled outside or off the monitoring cadence")
                break
            if slot in seen_slots:
                anomalies.append("duplicate scheduled attempt slot")
                break
            if slot != next_slot or stamp < scheduled:
                anomalies.append("attempt order or timestamp is inconsistent with monitoring cadence")
                break
            seen_slots.add(slot)
            next_slot += 1

    current = now or datetime.now(timezone.utc)
    event_end = max((e["_parsed_timestamp"] for e in events), default=None)
    stale = status == "running" and ((scheduled_end and current > scheduled_end) or (event_end and (current - event_end).total_seconds() > 3600))
    if stale:
        anomalies.append("status is running after scheduled end or last event; run may be stalled/incomplete")
    coverage = max(0.0, (min(event_end, scheduled_end) - started).total_seconds()) if started and scheduled_end and event_end else (0.0 if started and scheduled_end else None)
    timeline = []
    for event in attempts:
        if event["result"] == "success":
            continue
        routes = {}
        for name in ("local_route_to_board", "local_route_to_gateway"):
            info = event.get(name)
            if isinstance(info, dict):
                present = info.get("route_present")
                routes[name] = {"route_present": present if isinstance(present, bool) else None,
                                "interface": info.get("interface") if isinstance(info.get("interface"), str) else None}
        timeline.append({"timestamp_utc": event["timestamp_utc"], "category": event["result"], "routes": routes})

    no_anomalies = not anomalies
    complete_window = bool(summary and status == "completed" and started and scheduled_end and completed_at and
                           scheduled_end > started and completed_at >= scheduled_end and duration == 86400 and
                           (scheduled_end - started).total_seconds() == 86400 and interval and expected_attempts == duration // interval and
                           len(attempts) + skipped == expected_attempts and not mismatch and source_events_valid and no_anomalies)
    monitoring_complete = bool(summary and status == "completed" and started and scheduled_end and completed_at and
                               scheduled_end > started and completed_at >= scheduled_end and duration and
                               (scheduled_end - started).total_seconds() == duration and expected_attempts and
                               len(attempts) + skipped == expected_attempts and not mismatch and source_events_valid and no_anomalies)
    if complete_window:
        interpretation = "completed_24h_with_gaps" if skipped else "completed_24h"
    elif monitoring_complete:
        interpretation = "completed_window_with_gaps" if skipped else "completed_window"
    elif stale:
        interpretation = "possible_stalled_or_incomplete"
    elif status == "completed" and anomalies:
        interpretation = "inconsistent_completed_data"
    else:
        interpretation = "incomplete_or_unknown"
    return {
        "status": status, "interpretation": interpretation,
        "attempts_observed": len(attempts), "successes_observed": successes, "failures_observed": failures,
        "failure_categories": categories, "latency_seconds_successful_attempts": latency,
        "sampling_gaps": len(gaps), "missed_slots_observed": skipped,
        "elapsed_seconds_to_last_event": coverage, "expected_window_seconds": duration,
        "timeline": timeline[-MAX_TIMELINE:], "timeline_truncated": len(timeline) > MAX_TIMELINE,
        "anomalies": anomalies,
        "limitations": ["No board-vs-Wi-Fi cause is inferred.", "A route entry is not proof of Wi-Fi, gateway, or board reachability.", "No network observations occur during sleep or restart.", "This observes one Mac-to-board path.", "Elapsed time to last event is not continuous coverage or measured uptime.", "Completed monitoring does not imply every SSH attempt occurred when gaps are present.", "A 24-hour completed window is distinct from uninterrupted observations; zero SSH attempts cannot certify reliability."]
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_directory", help="monitor run directory containing summary.json and events.jsonl")
    parser.add_argument("--json", action="store_true", dest="as_json", help="emit machine-readable JSON")
    args = parser.parse_args(argv)
    result = analyze(args.run_directory)
    if args.as_json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(f"Status: {result['status']} ({result['interpretation']})")
        print(f"Attempts: {result['attempts_observed']} observed; {result['successes_observed']} successes; {result['failures_observed']} failures")
        print(f"Sampling gaps: {result['sampling_gaps']} ({result['missed_slots_observed']} missed slots)")
        latency = result["latency_seconds_successful_attempts"]
        if latency:
            print(f"Successful SSH latency: median={latency['median']:.3f}s, p95={latency['p95']:.3f}s")
        print(f"Elapsed to last event: {result['elapsed_seconds_to_last_event']} seconds (not continuous coverage)")
        for anomaly in result["anomalies"]:
            print(f"Anomaly: {anomaly}")
        for item in result["timeline"]:
            print(f"{item['timestamp_utc']} {item['category']} routes={json.dumps(item['routes'], sort_keys=True)}")
    return 1 if result["anomalies"] else 0


if __name__ == "__main__":
    sys.exit(main())
