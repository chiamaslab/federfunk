"""Power policy is independent of image success and device acknowledgement."""
from datetime import datetime, timedelta, timezone
from email.message import Message
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import render
import server


class PowerControlTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.feed = server.Feed("http://unused.invalid", Path(self.temp.name), assets=[])
        self.feed.settings = render.validate_settings({"schedule": {"enabled": False}}, self.feed.settings, [])
        stats = dict(date="2026-10-22", observed_at="2026-10-22T12:00:00-06:00", species=[], species_count=0, total_detections=0)
        self.feed.bundle = dict(frame=b"\x11" * 960000, art=b"art", preview=b"preview",
                                state=dict(settings=self.feed.settings, display_settings=self.feed.settings,
                                           stats=stats, display_stats=stats, print_generated_at=stats["observed_at"],
                                           revision="original", demo=False))
        server.save_bundle(self.feed.directory, self.feed.bundle)
        self.feed.archive(self.feed.bundle)

    def test_settings_default_validation_and_migration(self):
        defaults = render.validate_settings({}, {}, [])
        self.assertEqual(defaults["stop_voltage"], 3.3)
        self.assertEqual(defaults["update_mode"], "every_check")
        self.assertEqual(defaults["meaningful_max_minutes"], 180)
        self.assertEqual(defaults["meaningful_change_percent"], 10)
        self.assertEqual(defaults["refresh_interval_minutes"], 30)
        for voltage in (3.2, 3.25, 3.3, 3.35, 3.4, 3.45, 3.5, 3.55, 3.6):
            self.assertEqual(render.validate_settings({"stop_voltage": voltage}, {}, [])["stop_voltage"], voltage)
        for key, invalid in (("stop_voltage", (True, None, "3.3", 3.19, 3.61, 3.333, float("nan"), float("inf"), [])),
                             ("update_mode", (None, True, "sometimes", [], {})),
                             ("meaningful_max_minutes", (True, 29, 721, 60.0, "180", None)),
                             ("meaningful_change_percent", (True, 4, 51, 10.0, "10", None))):
            for value in invalid:
                with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                    render.validate_settings({key: value}, {}, [])
        old = dict(self.feed.settings)
        for key in ("stop_voltage", "update_mode", "meaningful_max_minutes", "meaningful_change_percent"):
            old.pop(key)
        self.feed.bundle["state"].update(settings=old, display_settings=old)
        self.feed.bundle["state"].pop("display_stats")
        self.feed.bundle["state"].pop("print_generated_at")
        server.save_bundle(self.feed.directory, self.feed.bundle)
        restored = server.Feed("http://unused.invalid", self.feed.directory, assets=[])
        self.assertEqual(restored.settings["stop_voltage"], 3.3)
        self.assertEqual(restored.bundle["state"]["display_stats"], restored.bundle["state"]["stats"])
        self.assertFalse(restored.state()["pending_settings"])

    def test_policy_only_edit_preserves_print_archive_and_battery(self):
        before = self.feed.bundle
        archive = self.feed.history.archived("2026-10-22", "download.zip")
        with patch.object(self.feed, "build", side_effect=AssertionError("Policy must not render")), \
                patch.object(self.feed, "archive", side_effect=AssertionError("Policy must not replace history")):
            state = self.feed.update_settings(dict(stop_voltage=3.35, update_mode="meaningful",
                                                  meaningful_max_minutes=240, meaningful_change_percent=15))
        for key in ("frame", "art", "preview"):
            self.assertEqual(before[key], self.feed.bundle[key])
        self.assertEqual(state["print_generated_at"], before["state"]["print_generated_at"])
        self.assertEqual(state["revision"], "original")
        self.assertFalse(state["pending_settings"])
        self.assertIsNone(state["device"]["stop_voltage"])
        self.assertEqual(self.feed.history.archived("2026-10-22", "download.zip"), archive)
        self.assertEqual(self.feed.history.battery(), [])

    def test_strict_optional_telemetry(self):
        tag = '"' + 'a' * 64 + '"'
        valid = {"X-FederFunk-Power-Control": "1", "X-FederFunk-Stop-Millivolts": "3300",
                 "X-FederFunk-Last-Displayed-ETag": tag, "X-FederFunk-Previous-Result": "failed",
                 "X-FederFunk-Previous-Sleep-Seconds": "86400"}
        data = server.power_headers(valid)
        self.assertTrue(data["power_control_supported"])
        self.assertEqual(data["stop_voltage"], 3.3)
        self.assertEqual(data["last_displayed_etag"], tag)
        self.assertEqual(data["previous_result"], "failed")
        self.assertEqual(data["previous_sleep_seconds"], 86400)
        for header, field, values in (
                ("X-FederFunk-Stop-Millivolts", "stop_voltage", ("3301", "3100", "3650", "3.3", "3300 ", "03300")),
                ("X-FederFunk-Last-Displayed-ETag", "last_displayed_etag", ('a'*64, 'W/'+tag, '"'+ 'A'*64+'"', tag+', '+tag)),
                ("X-FederFunk-Previous-Result", "previous_result", ("boot", "FAILED", " failed ")),
                ("X-FederFunk-Previous-Sleep-Seconds", "previous_sleep_seconds", ("86401", "-1", "1.5", "030", " 30", "9"*100))):
            for value in values:
                with self.subTest(header=header, value=value):
                    self.assertIsNone(server.power_headers({**valid, header: value})[field])
            duplicate = Message()
            for key, value in valid.items():
                duplicate[key] = value
            duplicate[header] = valid[header]
            self.assertIsNone(server.power_headers(duplicate)[field])
        for bad in (None, "true", " 1", "1,1", "2"):
            self.assertEqual(server.power_headers({**valid, "X-FederFunk-Power-Control": bad}), server.POWER_FIELDS)
        duplicate = Message()
        duplicate["X-FederFunk-Power-Control"] = "1"
        duplicate["X-FederFunk-Power-Control"] = "1"
        self.assertEqual(server.power_headers(duplicate), server.POWER_FIELDS)

    def test_acknowledgement_persistence_and_staleness_are_honest(self):
        self.feed.update_settings({"stop_voltage": 3.6})
        tag = render.etag(self.feed.bundle["frame"])
        headers = {"X-FederFunk-Power-Control": "1", "X-FederFunk-Stop-Millivolts": "3300",
                   "X-FederFunk-Last-Displayed-ETag": tag, "X-FederFunk-Previous-Result": "unchanged",
                   "X-FederFunk-Previous-Sleep-Seconds": "43200", "X-FederFunk-Battery-Voltage": "3.2"}
        self.feed.record_device(headers, at=datetime.now(timezone.utc) - timedelta(hours=2))
        status = self.feed.state()["device"]
        self.assertEqual(status["stop_voltage"], 3.3)  # Actual device, not newly saved 3.6V.
        self.assertTrue(status["last_displayed_matches_current"])
        self.assertTrue(status["stale"])  # Previous overnight sleep does not hide a missing daytime report.
        self.assertEqual(status["previous_result"], "unchanged")  # Low voltage alone never means confirmed pause.
        self.assertEqual(server.load_device(self.feed.directory), self.feed.device)
        self.feed.bundle = {**self.feed.bundle, "frame": b"\x22" * 960000}
        self.assertFalse(self.feed.state()["device"]["last_displayed_matches_current"])
        self.feed.record_device({"X-FederFunk-Power-Control": "1"})
        self.assertIsNone(self.feed.device["last_displayed_etag"])  # Cold boot omitted cached success.
        self.assertIsNone(self.feed.device["last_displayed_reported_at"])
        self.feed.record_device({})
        self.assertFalse(self.feed.device["power_control_supported"])

    def test_stop_header_200_304_and_hold_but_no_get_success_inference(self):
        http = ThreadingHTTPServer(("127.0.0.1", 0), server.handler_for(self.feed))
        thread = threading.Thread(target=http.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(http.server_close)
        self.addCleanup(http.shutdown)
        base = "http://127.0.0.1:" + str(http.server_port)
        endpoint = base + "/api/v1/display/current_large.bin"
        headers = {"User-Agent": "ESP32HTTPClient", "X-FederFunk-Power-Control": "1"}
        with urlopen(Request(endpoint, headers=headers), timeout=5) as response:
            self.assertEqual(response.headers["X-FederFunk-Stop-Millivolts"], "3300")
            tag = response.headers["ETag"]
            response.read()
        self.assertFalse(self.feed.state()["device"]["last_displayed_matches_current"])
        self.feed.update_settings({"stop_voltage": 3.35})
        with patch.object(self.feed, "frame_for_display", return_value=(self.feed.bundle, 40000)):
            with self.assertRaises(HTTPError) as result:
                urlopen(Request(endpoint, headers={**headers, "If-None-Match": tag}), timeout=5)
            self.assertEqual(result.exception.code, 304)
            self.assertEqual(result.exception.headers["X-FederFunk-Stop-Millivolts"], "3350")
            self.assertEqual(result.exception.headers["X-FederFunk-Hold-Seconds"], "40000")
            result.exception.close()
        self.assertFalse(self.feed.state()["device"]["last_displayed_matches_current"])
        for path in ("/art.png", "/preview.png", "/api/state"):
            with urlopen(base + path, timeout=5) as response:
                self.assertIsNone(response.headers.get("X-FederFunk-Stop-Millivolts"))
        with patch.object(self.feed, "frame_for_display", side_effect=OSError("offline")):
            with self.assertRaises(HTTPError) as result:
                urlopen(Request(endpoint, headers=headers), timeout=5)
            self.assertEqual(result.exception.code, 503)
            self.assertIsNone(result.exception.headers.get("X-FederFunk-Stop-Millivolts"))
            result.exception.close()


if __name__ == "__main__":
    unittest.main()
