from datetime import datetime, timedelta, timezone
import io
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from history_store import HistoryStore, battery_csv, discharge_estimate


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name)
        self.store = HistoryStore(self.path)
        self.now = datetime(2026, 1, 2, 12, tzinfo=timezone.utc)

    def tearDown(self):
        self.temp.cleanup()

    def bundle(self, at, revision="one"):
        return dict(art=b"art", preview=b"preview", frame=b"frame", state=dict(revision=revision,
                    stats=dict(date=at.date().isoformat(), observed_at=at.isoformat(), species_count=3, total_detections=20),
                    settings=dict(style="collage"), credits=[{"creator": "Artist", "_resolved_path": "/private/asset"}]))

    def test_archive_keeps_latest_of_each_day_and_survives_restart(self):
        self.store.archive(self.bundle(self.now))
        self.store.archive(self.bundle(self.now+timedelta(hours=1), "new"))
        self.store.archive(self.bundle(self.now-timedelta(hours=1), "old"))
        self.store.archive(self.bundle(self.now-timedelta(days=1), "yesterday"))
        restored = HistoryStore(self.path)
        self.assertEqual([d["revision"] for d in restored.days()], ["new", "yesterday"])
        data = restored.archived("2026-01-02", "download.zip")
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            self.assertEqual(set(archive.namelist()), {"state.json", "art.png", "preview.png", "current_large.bin"})
            state = json.loads(archive.read("state.json"))
        self.assertNotIn("/private", json.dumps(state))
        self.assertEqual(state["credits"][0]["creator"], "Artist")
        self.assertIsNone(restored.archived("2026-01-03", "art.png"))
        for value in ("../private", "2026-02-30", "2026-1-2"):
            with self.assertRaises(ValueError):
                restored.archived(value, "art.png")

    def test_battery_requires_complete_valid_readings_and_bounds_frequency(self):
        reading = dict(battery_percent=80, battery_voltage=4.0, charging="not_charging")
        for bad in ({}, {"battery_percent": 50}, {**reading, "battery_percent": True},
                    {**reading, "battery_voltage": float("nan")}, {**reading, "charging": "full"}):
            self.assertFalse(self.store.add_battery(bad, self.now))
        self.assertTrue(self.store.add_battery(reading, self.now))
        self.assertFalse(self.store.add_battery(reading, self.now+timedelta(seconds=30)))
        self.assertTrue(self.store.add_battery(reading, self.now+timedelta(seconds=60)))
        rows = self.store.battery(7, self.now+timedelta(minutes=2))
        self.assertEqual(len(rows), 2)
        self.assertIn(b"at,battery_percent,battery_voltage,charging", battery_csv(rows))
        self.assertEqual(HistoryStore(self.path).battery(30, self.now+timedelta(minutes=2)), rows)
        for days in (0, 8, 365, True, "30"):
            with self.assertRaises(ValueError):
                self.store.battery(days, self.now)

    def test_charge_state_transitions_are_kept_even_between_rapid_manual_wakes(self):
        values = dict(battery_percent=70, battery_voltage=3.9, charging="not_charging")
        self.store.add_battery(values, self.now)
        self.assertTrue(self.store.add_battery({**values, "charging": "charging"}, self.now+timedelta(seconds=10)))
        self.assertTrue(self.store.add_battery(values, self.now+timedelta(seconds=20)))
        self.assertEqual([r["charging"] for r in self.store.battery(7, self.now+timedelta(minutes=1))],
                         ["not_charging", "charging", "not_charging"])

    def readings(self):
        return [dict(at=(self.now-timedelta(hours=9-i)).isoformat(), battery_percent=80-i,
                     battery_voltage=4.1-.009*i, charging="not_charging") for i in range(10)]

    def test_estimate_needs_consistent_fresh_decline_and_reports_rough_ranges(self):
        rows = self.readings()
        result = discharge_estimate(rows, self.now)
        self.assertTrue(result["available"])
        self.assertLess(result["hours_to_15"]["low"], result["hours_to_15"]["high"])
        self.assertGreater(result["hours_to_zero"]["high"], result["hours_to_15"]["high"])
        self.assertIn("not a prediction", result["caveat"])
        self.assertFalse(discharge_estimate(rows[:3], self.now)["available"])
        self.assertFalse(discharge_estimate(rows, self.now+timedelta(hours=2))["available"])
        flat = [{**r, "battery_percent": 80, "battery_voltage": 4.0} for r in rows]
        self.assertFalse(discharge_estimate(flat, self.now)["available"])

    def test_forecast_resets_on_charging_unknown_recharge_and_gaps(self):
        for field, value in (("charging", "charging"), ("charging", "unknown"),
                             ("battery_percent", 100), ("battery_voltage", 4.4)):
            rows = self.readings()
            rows[-2][field] = value
            self.assertFalse(discharge_estimate(rows, self.now)["available"], (field, value))
        rows = self.readings()
        rows[-1]["at"] = (self.now+timedelta(hours=4)).isoformat()
        self.assertFalse(discharge_estimate(rows, self.now+timedelta(hours=4))["available"])

    def test_forecast_freshness_follows_two_hour_device_interval(self):
        rows = [{**row, "at": (self.now-timedelta(hours=2*(9-i))).isoformat()}
                for i, row in enumerate(self.readings())]
        current = self.now+timedelta(hours=2, minutes=10)
        self.assertFalse(discharge_estimate(rows, current)["available"])
        result = discharge_estimate(rows, current, expected_interval_seconds=7200)
        self.assertTrue(result["available"])
        self.assertEqual(result["sample_count"], 10)
        # Two expected wakes plus a 15-minute processing allowance; no estimate
        # once the readings are older than this bounded tolerance.
        boundary = self.now+timedelta(hours=4, minutes=15)
        self.assertTrue(discharge_estimate(rows, boundary, expected_interval_seconds=7200)["available"])
        expired = discharge_estimate(rows, boundary+timedelta(seconds=1), expected_interval_seconds=7200)
        self.assertEqual(expired["reason"], "Latest reading is too old for a forecast")

    def test_forecast_keeps_sparse_segments_without_weakening_sample_requirements(self):
        rows = [dict(at=(self.now-timedelta(hours=44-12*i)).isoformat(), battery_percent=80-3*i,
                     battery_voltage=4.1-.03*i, charging="not_charging") for i in range(4)]
        result = discharge_estimate(rows, self.now, expected_interval_seconds=43200)
        self.assertEqual(result["sample_count"], 4)
        self.assertEqual(result["span_hours"], 36)
        self.assertFalse(result["available"])
        self.assertEqual(result["reason"], "More consistent readings are needed")
        self.assertEqual(discharge_estimate(rows, self.now)["sample_count"], 1)

    def test_interval_aware_forecast_still_breaks_on_missed_wakes_and_charge_changes(self):
        rows = self.readings()
        boundary = self.now+timedelta(hours=5, minutes=15)
        rows[-1]["at"] = boundary.isoformat()  # 6 h 15 min after the previous report.
        self.assertEqual(discharge_estimate(rows, boundary, expected_interval_seconds=7200)["sample_count"], 10)
        rows[-1]["at"] = (boundary+timedelta(seconds=1)).isoformat()
        self.assertEqual(discharge_estimate(rows, boundary+timedelta(seconds=1),
                                            expected_interval_seconds=7200)["sample_count"], 1)
        for field, value in (("charging", "charging"), ("charging", "unknown"),
                             ("battery_percent", 100), ("battery_voltage", 4.4)):
            rows = self.readings()
            rows[-2][field] = value
            self.assertFalse(discharge_estimate(rows, self.now, expected_interval_seconds=43200)["available"],
                             (field, value))

    def test_forecast_rejects_invalid_intervals_and_keeps_default_tolerances(self):
        rows = self.readings()
        current = self.now+timedelta(minutes=76)
        fallback = discharge_estimate(rows, current)
        for interval in (None, True, -1, 0, 299, 43201, 1_000_000_000, 1800.0, "7200",
                         float("nan"), float("inf"), {}, []):
            with self.subTest(interval=interval):
                self.assertEqual(discharge_estimate(rows, current, expected_interval_seconds=interval), fallback)
        self.assertTrue(discharge_estimate(rows, self.now+timedelta(minutes=75))["available"])
        self.assertEqual(discharge_estimate(rows, current, expected_interval_seconds=300), fallback)
        rows[-1]["at"] = (self.now+timedelta(hours=2)).isoformat()  # Original 3-hour gap limit.
        self.assertEqual(discharge_estimate(rows, self.now+timedelta(hours=2))["sample_count"], 10)
        rows[-1]["at"] = (self.now+timedelta(hours=2, seconds=1)).isoformat()
        self.assertEqual(discharge_estimate(rows, self.now+timedelta(hours=2, seconds=1))["sample_count"], 1)

    def test_partial_report_breaks_forecast_without_inventing_chart_values(self):
        rows = self.readings()
        for row in rows:
            self.store.add_battery(row, datetime.fromisoformat(row["at"]))
        self.assertTrue(discharge_estimate(self.store.forecast_rows(rows), self.now)["available"])
        self.assertFalse(self.store.add_battery({"charging": "unknown"}, self.now+timedelta(seconds=10)))
        current = self.now+timedelta(minutes=30)
        self.store.add_battery(dict(battery_percent=70, battery_voltage=4.0, charging="not_charging"), current)
        restored = HistoryStore(self.path)
        history = restored.battery(7, current)
        self.assertEqual(len(history), 11)
        self.assertEqual(len(restored.forecast_rows(history)), 1)
        self.assertFalse(discharge_estimate(restored.forecast_rows(history), current)["available"])

    def test_capture_import_backfills_once_uses_measurement_time_and_ignores_poll_rows(self):
        path = self.path/"capture.jsonl"
        start = self.now-timedelta(hours=2)
        rows = [dict(event="test_started", start_at=start.isoformat(), end_at=(start+timedelta(hours=96)).isoformat()),
                dict(event="poll", status="cached", observed_at=self.now.isoformat()),
                dict(event="measurement", reported_at=start.isoformat(), observed_at=self.now.isoformat(),
                     battery_percent=90, battery_voltage=4.1, charging="not_charging")]
        path.write_text("\n".join(json.dumps(r) for r in rows)+"\n")
        self.store.add_battery(dict(battery_percent=80, battery_voltage=4.0), self.now)
        self.assertEqual(self.store.import_capture(path), 1)
        self.assertEqual(self.store.import_capture(path), 0)
        history = self.store.battery(7, self.now)
        self.assertEqual(len(history), 2)
        self.assertEqual(history[0]["at"], start.isoformat())
        # Validate the entire file before committing anything from a bad import.
        rows[-1]["battery_voltage"] = 20
        path.write_text("\n".join(json.dumps(r) for r in rows)+"\n")
        with self.assertRaises(ValueError):
            self.store.import_capture(path)
        self.assertEqual(self.store.battery(7, self.now), history)


if __name__ == "__main__":
    unittest.main()
