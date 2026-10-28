"""Refresh timing never changes print pixels, history or legacy device cadence."""
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


class RefreshIntervalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.feed = server.Feed("http://unused.invalid", Path(self.temp.name), assets=[], preview_only=True)
        self.feed.settings = render.validate_settings({"schedule": {"enabled": False}}, self.feed.settings, [])
        self.feed.bundle = dict(frame=b"\x11" * 960000, art=b"art", preview=b"preview",
                                state=dict(settings=self.feed.settings, display_settings=self.feed.settings,
                                           revision="same-pixels", demo=False,
                                           stats=dict(date="2026-10-22", observed_at="2026-10-22T12:00:00-06:00",
                                                      species=[], species_count=0, total_detections=0)))
        server.save_bundle(self.feed.directory, self.feed.bundle)
        self.feed.archive(self.feed.bundle)

    def test_defaults_validation_and_migration(self):
        self.assertEqual(render.validate_settings({}, {}, [])["refresh_interval_minutes"], 30)
        for value in (5, 30, 60, 120, 45, 720):
            self.assertEqual(render.validate_settings({"refresh_interval_minutes": value}, {}, [])[
                "refresh_interval_minutes"], value)
        for value in (None, True, 0, 4, 721, 30.0, "60", [], {}, float("nan")):
            with self.subTest(value=value), self.assertRaises(ValueError):
                render.validate_settings({"refresh_interval_minutes": value}, {}, [])
        self.feed.bundle["state"]["settings"].pop("refresh_interval_minutes")
        server.save_bundle(self.feed.directory, self.feed.bundle)
        restored = server.Feed("http://unused.invalid", self.feed.directory, assets=[])
        self.assertEqual(restored.settings["refresh_interval_minutes"], 30)
        self.assertFalse(restored.state()["pending_settings"])

    def test_timing_edit_preserves_exact_images_archive_and_battery_history(self):
        original = self.feed.bundle
        archive = self.feed.history.archived("2026-10-22", "download.zip")
        with patch.object(self.feed, "build", side_effect=AssertionError("Timing must not render")), \
                patch.object(self.feed, "archive", side_effect=AssertionError("Timing must not archive")):
            result = self.feed.update_settings({"refresh_interval_minutes": 60})
        for key in ("frame", "art", "preview"):
            self.assertEqual(self.feed.bundle[key], original[key])
        self.assertEqual(result["revision"], original["state"]["revision"])
        self.assertFalse(result["pending_settings"])
        self.assertEqual(self.feed.history.archived("2026-10-22", "download.zip"), archive)
        self.assertEqual(self.feed.history.battery(), [])
        restored = server.Feed("http://unused.invalid", self.feed.directory, assets=[])
        self.assertEqual(restored.settings["refresh_interval_minutes"], 60)

    def test_failed_save_leaves_timing_unchanged(self):
        original = self.feed.bundle
        with patch.object(server, "save_bundle", side_effect=OSError("disk full")), self.assertRaises(OSError):
            self.feed.update_settings({"refresh_interval_minutes": 120})
        self.assertIs(self.feed.bundle, original)
        self.assertEqual(self.feed.settings["refresh_interval_minutes"], 30)

    def test_overnight_timing_edit_retains_queued_artwork_edits(self):
        inactive = dict(active=False, sleep_seconds=3600)
        with patch.object(server, "schedule_state", return_value=inactive):
            self.feed.update_settings({"show_counts": True})
            result = self.feed.update_settings({"refresh_interval_minutes": 120})
        self.assertTrue(result["pending_settings"])
        self.assertFalse(self.feed.bundle["state"]["display_settings"]["show_counts"])
        self.assertEqual(result["settings"]["refresh_interval_minutes"], 120)

    def test_capability_is_strict_and_last_offered_interval_survives_restart(self):
        name = "X-FederFunk-Refresh-Control"
        for value in (None, "", "0", "true", "2", " 1", "1,1"):
            self.assertFalse(server.refresh_control_supported({name: value}))
        duplicate = Message()
        duplicate[name] = "1"
        duplicate[name] = "1"
        self.assertFalse(server.refresh_control_supported(duplicate))
        self.feed.update_settings({"refresh_interval_minutes": 120})
        self.feed.record_device({name: "1", "X-FederFunk-Battery-Percent": "70"}, refresh_seconds=7200)
        self.assertTrue(self.feed.device["refresh_control_supported"])
        self.assertEqual(self.feed.device["refresh_interval_seconds"], 7200)
        self.feed.update_settings({"refresh_interval_minutes": 30})
        self.assertEqual(self.feed.device["refresh_interval_seconds"], 7200)
        self.assertEqual(server.load_device(self.feed.directory), self.feed.device)
        self.feed.record_device({})
        self.assertFalse(self.feed.device["refresh_control_supported"])
        self.assertEqual(self.feed.device["refresh_interval_seconds"], 1800)

    def test_monitor_follows_device_cadence_not_unreceived_dashboard_edit(self):
        self.feed.update_settings({"refresh_interval_minutes": 120})
        self.feed.record_device({"X-FederFunk-Battery-Percent": "70"})
        self.feed.device["reported_at"] = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        self.assertTrue(self.feed.state()["device"]["stale"])
        self.feed.record_device({"X-FederFunk-Refresh-Control": "1"}, refresh_seconds=7200)
        self.assertFalse(self.feed.state()["device"]["stale"])
        self.feed.update_settings({"refresh_interval_minutes": 30})
        self.assertFalse(self.feed.state()["device"]["stale"])
        with patch.object(server, "discharge_estimate", return_value={}) as forecast:
            self.feed.battery_state(30)
        self.assertEqual(forecast.call_args.kwargs["expected_interval_seconds"], 7200)

    def test_http_headers_on_200_and_304_and_overnight_precedence(self):
        http = ThreadingHTTPServer(("127.0.0.1", 0), server.handler_for(self.feed))
        thread = threading.Thread(target=http.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(http.server_close)
        self.addCleanup(http.shutdown)
        url = "http://127.0.0.1:" + str(http.server_port)
        self.feed.update_settings({"refresh_interval_minutes": 45})
        endpoint = url + "/api/v1/display/current_large.bin"
        with urlopen(endpoint, timeout=5) as response:
            self.assertEqual(response.headers["X-FederFunk-Refresh-Seconds"], "2700")
            tag = response.headers["ETag"]
            self.assertEqual(response.read(), self.feed.bundle["frame"])
        with self.assertRaises(HTTPError) as result:
            urlopen(Request(endpoint, headers={"If-None-Match": tag}), timeout=5)
        self.assertEqual(result.exception.code, 304)
        self.assertEqual(result.exception.headers["X-FederFunk-Refresh-Seconds"], "2700")
        result.exception.close()
        with patch.object(self.feed, "schedule", return_value=dict(active=False, sleep_seconds=40000)):
            with urlopen(endpoint, timeout=5) as response:
                self.assertEqual(response.headers["X-FederFunk-Hold-Seconds"], "40000")
                self.assertIsNone(response.headers.get("X-FederFunk-Refresh-Seconds"))
        for path in ("/art.png", "/preview.png", "/api/state"):
            with urlopen(url + path, timeout=5) as response:
                self.assertIsNone(response.headers.get("X-FederFunk-Refresh-Seconds"))
        self.assertIsNone(self.feed.device["last_seen"])

        device_headers = {"User-Agent": "ESP32HTTPClient", "X-FederFunk-Refresh-Control": "1"}
        self.feed.resume_pending = False
        with urlopen(Request(endpoint, headers=device_headers), timeout=5) as response:
            offered = int(response.headers["X-FederFunk-Refresh-Seconds"])
            self.assertEqual(self.feed.device["refresh_interval_seconds"], offered)
        self.feed.update_settings({"refresh_interval_minutes": 720})
        with patch.object(self.feed, "frame_for_display", return_value=(self.feed.bundle, 40000)):
            with urlopen(Request(endpoint, headers=device_headers), timeout=5):
                self.assertEqual(self.feed.device["refresh_interval_seconds"], offered)
        with patch.object(self.feed, "frame_for_display", side_effect=OSError("Upstream unavailable")):
            with self.assertRaises(HTTPError) as result:
                urlopen(Request(endpoint, headers=device_headers), timeout=5)
            self.assertEqual(result.exception.code, 503)
            self.assertIsNone(result.exception.headers.get("X-FederFunk-Refresh-Seconds"))
            result.exception.close()
            self.assertEqual(self.feed.device["refresh_interval_seconds"], 1800)


if __name__ == "__main__":
    unittest.main()
