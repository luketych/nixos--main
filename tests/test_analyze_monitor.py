"""Synthetic, offline tests for the monitor report analyzer."""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest

from scripts.network_resilience.analyze_monitor import MAX_FILE_BYTES, analyze


class AnalyzeMonitorTests(unittest.TestCase):
    START = "2026-11-03T00:00:00Z"

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def fixture(self, status="running", events=(), *, slots=288, interval=300, duration=None, **updates):
        duration = duration if duration is not None else slots * interval
        end = datetime.fromisoformat(self.START.replace("Z", "+00:00")).timestamp() + duration
        scheduled_end = datetime.fromtimestamp(end, timezone.utc).isoformat().replace("+00:00", "Z")
        summary = {"schema_version": 1, "status": status, "started_utc": self.START,
                   "scheduled_end_utc": scheduled_end,
                   "completed_utc": scheduled_end if status == "completed" else None,
                   "expected_attempts": slots, "interval_seconds": interval, "duration_seconds": duration,
                   "attempts": sum(e.get("type") == "ssh_attempt" for e in events),
                   "successes": sum(e.get("type") == "ssh_attempt" and e.get("result") == "success" for e in events),
                   "failures": sum(e.get("type") == "ssh_attempt" and e.get("result") != "success" for e in events),
                   "skipped_slots": sum(e.get("missed_slots", 0) for e in events if e.get("type") == "sampling_gap")}
        summary.update(updates)
        (self.directory / "summary.json").write_text(json.dumps(summary))
        (self.directory / "events.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events))

    @staticmethod
    def attempt(timestamp, result="success", elapsed=1.0, **extra):
        return {"type": "ssh_attempt", "timestamp_utc": timestamp, "scheduled_utc": timestamp,
                "lateness_seconds": 0, "result": result, "elapsed_seconds": elapsed, **extra}

    def test_running_success_has_coverage_and_is_not_completed(self):
        now = datetime.fromisoformat(self.START.replace("Z", "+00:00")) + timedelta(minutes=5)
        event = self.attempt(self.START)
        event["timestamp_utc"] = (now - timedelta(minutes=4)).isoformat()
        self.fixture(events=[event])
        result = analyze(self.directory, now=now)
        self.assertEqual(result["anomalies"], [])
        self.assertEqual(result["successes_observed"], 1)
        self.assertEqual(result["interpretation"], "incomplete_or_unknown")
        self.assertIsNotNone(result["elapsed_seconds_to_last_event"])
        self.assertIn("not continuous coverage", " ".join(result["limitations"]))

    def test_completed_short_window_with_gap_is_not_claimed_as_24h(self):
        events = [self.attempt("2026-11-03T00:00:00Z"), {"type": "sampling_gap", "timestamp_utc": "2026-11-03T00:20:00Z", "missed_slots": 3, "reason": "window-elapsed-before-slot"}]
        self.fixture("completed", events, slots=4, interval=300, duration=1200, attempts=1, successes=1, failures=0, skipped_slots=3)
        result = analyze(self.directory)
        self.assertEqual(result["interpretation"], "completed_window_with_gaps")
        self.assertEqual(result["failures_observed"], 0)
        self.assertEqual(result["missed_slots_observed"], 3)
        self.assertIn("every SSH attempt occurred", " ".join(result["limitations"]))

    def test_realistic_completed_24h_requires_consistent_window_and_counts(self):
        start = datetime.fromisoformat(self.START.replace("Z", "+00:00"))
        events = [self.attempt((start + timedelta(seconds=300 * i)).isoformat()) for i in range(288)]
        self.fixture("completed", events)
        result = analyze(self.directory)
        self.assertEqual(result["interpretation"], "completed_24h")
        self.assertEqual(result["attempts_observed"], 288)

    def test_completed_24h_with_gaps_is_distinguished(self):
        events = [self.attempt("2026-11-03T00:00:00Z"), {"type": "sampling_gap", "timestamp_utc": "2026-11-04T00:00:00Z", "missed_slots": 287, "reason": "window-elapsed-before-slot"}]
        self.fixture("completed", events, attempts=1, successes=1, failures=0, skipped_slots=287)
        self.assertEqual(analyze(self.directory)["interpretation"], "completed_24h_with_gaps")

    def test_failure_routes_keep_strict_tristate_and_latency_uses_success_only(self):
        events = [self.attempt("2026-11-03T00:00:00Z", "timeout", elapsed=12, local_route_to_board={"route_present": 1, "interface": "en0"}), self.attempt("2026-11-03T00:05:00Z", elapsed=2)]
        self.fixture(events=events, slots=288, attempts=2, successes=1, failures=1)
        result = analyze(self.directory)
        self.assertIsNone(result["timeline"][0]["routes"]["local_route_to_board"]["route_present"])
        self.assertEqual(result["latency_seconds_successful_attempts"]["median"], 2)
        self.assertIn("No board-vs-Wi-Fi cause is inferred.", result["limitations"])

    def test_trailing_fragment_is_flagged_incomplete(self):
        self.fixture(events=[self.attempt("2026-11-03T01:00:00Z")])
        with (self.directory / "events.jsonl").open("a") as file:
            file.write('{"type":"ssh_attempt"')
        result = analyze(self.directory)
        self.assertEqual(result["attempts_observed"], 1)
        self.assertTrue(any("unterminated" in item for item in result["anomalies"]))

    def test_malformed_line_and_invalid_event_fields_are_anomalies(self):
        events = [self.attempt("2026-11-03T01:00:00Z", elapsed=-1), {"type": "other", "timestamp_utc": "2026-11-03T01:00:00Z"}]
        self.fixture("completed", events)
        with (self.directory / "events.jsonl").open("a") as file:
            file.write("not-json\n")
        result = analyze(self.directory)
        self.assertTrue(any("invalid elapsed_seconds" in item for item in result["anomalies"]))
        self.assertTrue(any("unknown event type" in item for item in result["anomalies"]))
        self.assertTrue(any("malformed complete" in item for item in result["anomalies"]))
        self.assertEqual(result["interpretation"], "inconsistent_completed_data")

    def test_stale_running_uses_real_schedule_keys_and_mock_time(self):
        now = datetime(2026, 11, 5, tzinfo=timezone.utc)
        event = self.attempt("2026-11-03T01:00:00Z")
        self.fixture("running", [event], attempts=99)
        result = analyze(self.directory, now=now)
        self.assertEqual(result["interpretation"], "possible_stalled_or_incomplete")
        self.assertTrue(any("snapshot race" in item for item in result["anomalies"]))

    def test_completed_counter_mismatch_is_inconsistent_not_snapshot_race(self):
        event = self.attempt("2026-11-03T01:00:00Z")
        self.fixture("completed", [event], attempts=2)
        result = analyze(self.directory)
        self.assertEqual(result["interpretation"], "inconsistent_completed_data")
        self.assertTrue(any("inconsistent" in item for item in result["anomalies"]))
        self.assertFalse(any("snapshot race" in item for item in result["anomalies"]))

    def test_duplicate_or_out_of_window_schedule_cannot_certify_completion(self):
        duplicate = [self.attempt(self.START), self.attempt(self.START)]
        self.fixture("completed", duplicate, slots=2, interval=300)
        result = analyze(self.directory)
        self.assertIn("duplicate scheduled attempt slot", result["anomalies"])
        self.assertEqual(result["interpretation"], "inconsistent_completed_data")
        outside = [self.attempt(self.START)]
        outside[0]["scheduled_utc"] = "2026-11-04T00:00:00Z"
        self.fixture("completed", outside, slots=1, interval=300)
        self.assertIn("attempt scheduled outside or off the monitoring cadence", analyze(self.directory)["anomalies"])

    def test_invalid_schema_and_zero_observed_attempts_cannot_complete(self):
        self.fixture("completed", [], attempts=0, successes=0, failures=0, skipped_slots=0)
        result = analyze(self.directory)
        self.assertNotEqual(result["interpretation"], "completed_24h")
        self.assertTrue(any("do not equal expected_attempts" in item for item in result["anomalies"]))
        self.fixture("completed", [self.attempt("2026-11-03T00:00:00Z")], schema_version=2)
        self.assertTrue(any("schema_version" in item for item in analyze(self.directory)["anomalies"]))

    def test_outside_window_gap_cannot_certify_a_completed_run(self):
        gap = {"type": "sampling_gap", "timestamp_utc": "2030-01-01T00:00:00Z", "missed_slots": 288, "reason": "window-elapsed-before-slot"}
        self.fixture("completed", [gap], attempts=0, successes=0, failures=0, skipped_slots=288)
        result = analyze(self.directory)
        self.assertEqual(result["interpretation"], "inconsistent_completed_data")
        self.assertIn("event timestamp outside observed run", result["anomalies"])

    def test_gap_slot_count_must_match_time_and_order(self):
        first = self.attempt(self.START)
        invalid_gap = {"type": "sampling_gap", "timestamp_utc": "2026-11-03T00:10:00Z", "missed_slots": 2, "reason": "scheduler-late-or-sleep"}
        self.fixture("completed", [first, invalid_gap], slots=3, interval=300)
        result = analyze(self.directory)
        self.assertIn("sampling gap count does not match its timestamp/cadence", result["anomalies"])
        self.assertEqual(result["interpretation"], "inconsistent_completed_data")

    def test_invalid_gap_counts_are_flagged_not_summed(self):
        self.fixture(events=[])
        gap = {"type": "sampling_gap", "timestamp_utc": self.START, "missed_slots": "not-a-number"}
        (self.directory / "events.jsonl").write_text(json.dumps(gap) + "\n")
        result = analyze(self.directory)
        self.assertTrue(any("invalid missed_slots" in x for x in result["anomalies"]))
        self.assertEqual(result["missed_slots_observed"], 0)

    def test_invalid_status_type_does_not_crash(self):
        self.fixture(status={"unexpected": True})
        self.assertIn("invalid status", analyze(self.directory)["anomalies"])

    def test_missing_invalid_and_oversized_inputs_are_anomalies(self):
        result = analyze(self.directory)
        self.assertTrue(any("missing summary.json" in item for item in result["anomalies"]))
        self.assertTrue(any("missing events.jsonl" in item for item in result["anomalies"]))
        self.fixture()
        (self.directory / "events.jsonl").write_bytes(b"x" * (MAX_FILE_BYTES + 1))
        self.assertTrue(any("safety limit" in item for item in analyze(self.directory)["anomalies"]))


if __name__ == "__main__":
    unittest.main()
