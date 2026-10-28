from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import battery_capture as capture


class CaptureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name)/"capture.jsonl"
        self.start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        self.collector = capture.Capture(self.path, self.start)

    def tearDown(self):
        self.temp.cleanup()

    def state(self, at):
        return {"device": {"reported_at": at.isoformat(), "battery_percent": 90,
                           "battery_voltage": 4.1, "charging": "not_charging"}, "secret": "never copy"}

    def rows(self):
        return [json.loads(line) for line in self.path.read_text().splitlines()]

    def test_excludes_old_cache_and_deduplicates_after_restart(self):
        self.collector.observe(self.state(self.start-timedelta(minutes=1)), self.start)
        self.assertEqual(self.rows()[-1]["status"], "cached_before_test")
        self.collector.observe(self.state(self.start), self.start+timedelta(minutes=1))
        restored = capture.Capture(self.path, self.start)
        restored.observe(self.state(self.start), self.start+timedelta(minutes=2))
        samples = [r for r in self.rows() if r["event"] == "measurement"]
        self.assertEqual(len(samples), 1)
        self.assertNotEqual(samples[0]["reported_at"], samples[0]["observed_at"])
        self.assertNotIn("secret", self.path.read_text())
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_no_demo_invalid_or_outside_window_samples(self):
        values = [dict(self.state(self.start), demo=True), {"device": {}},
                  self.state(self.start+timedelta(days=5))]
        for state in values:
            self.collector.observe(state, self.start)
        self.assertFalse(any(row["event"] == "measurement" for row in self.rows()))

    def test_only_local_state_endpoint_no_frame_or_external_url(self):
        for url in ("http://192.0.2.5/api/state", "http://localhost:8001/api/v1/display/current_large.bin",
                    "http://user:secret@localhost/api/state", "file:///api/state"):
            with self.assertRaises(ValueError), patch.object(capture, "build_opener") as opener:
                capture.fetch_state(url)
            opener.assert_not_called()
        self.assertEqual(capture.validate_url("http://127.0.0.1:8001/api/state"), "http://127.0.0.1:8001/api/state")
        self.assertIsNone(capture.NoRedirect().redirect_request(None, None, None, None, None, None))

    def test_restart_recovers_only_incomplete_tail_and_rejects_other_test(self):
        with self.path.open("ab") as output:
            output.write(b'{"unfinished":')
        capture.Capture(self.path, self.start)
        self.assertEqual(len(self.rows()), 1)
        with self.assertRaises(ValueError):
            capture.Capture(self.path, self.start+timedelta(days=1))
        with self.assertRaises(ValueError):
            capture.Capture(self.path, self.start, 97)

    def test_extend_existing_test_keeps_original_records_and_deduplicates(self):
        self.collector.observe(self.state(self.start), self.start)
        original = self.path.read_bytes()
        extended = capture.Capture(self.path, self.start, hours=None)
        self.assertIsNone(extended.end)
        self.assertTrue(self.path.read_bytes().startswith(original))
        update = self.rows()[-1]
        self.assertEqual(update["event"], "test_window_updated")
        self.assertEqual(update["previous_end_at"], (self.start+timedelta(hours=96)).isoformat())
        self.assertIsNone(update["end_at"])
        self.assertFalse(extended.observe(self.state(self.start), self.start+timedelta(minutes=1)))
        later = self.start+timedelta(days=8)
        self.assertTrue(extended.observe(self.state(later), later))
        restored = capture.Capture(self.path, self.start, hours=None)
        self.assertFalse(restored.observe(self.state(later), later+timedelta(minutes=1)))
        self.assertEqual(len([r for r in self.rows() if r["event"] == "test_window_updated"]), 1)
        self.assertEqual(len([r for r in self.rows() if r["event"] == "measurement"]), 2)

    def test_open_ended_test_still_rejects_old_and_invalid_measurements(self):
        extended = capture.Capture(self.path, self.start, hours=None)
        later = self.start+timedelta(days=8)
        self.assertFalse(extended.observe(self.state(self.start-timedelta(minutes=1)), later))
        self.assertEqual(self.rows()[-1]["status"], "cached_before_test")
        self.assertFalse(extended.observe(self.state(later+timedelta(minutes=10)), later))
        self.assertEqual(self.rows()[-1]["status"], "no_valid_measurement")

    def test_open_ended_main_runs_after_old_deadline_without_ending_test(self):
        self.collector.observe(self.state(self.start), self.start)
        later = self.start+timedelta(days=8)
        args = ["battery_capture.py", "--output", str(self.path), "--start", self.start.isoformat(),
                "--until-stopped", "--once"]
        with patch("sys.argv", args), patch.object(capture, "fetch_state", return_value=self.state(later)), \
                patch.object(capture, "datetime") as clock:
            clock.now.return_value = later
            clock.fromisoformat.side_effect = datetime.fromisoformat
            capture.main()
        self.assertEqual(self.rows()[-1]["status"], "new_measurement")
        self.assertFalse(any(r["event"] == "test_window_ended" for r in self.rows()))


if __name__ == "__main__":
    unittest.main()
