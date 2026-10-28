from datetime import datetime, timezone
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


class HistoryHttpTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.feed = server.Feed("http://unused", Path(self.temp.name), assets=[], demo=True, preview_only=True)
        self.feed.settings = render.validate_settings({"schedule": {"enabled": False}}, self.feed.settings, [])
        self.feed.bundle = self.feed.build([], "2026-01-02", self.feed.settings, "2026-01-02T12:00:00+00:00")
        self.feed.history.archive(self.feed.bundle)
        self.http = ThreadingHTTPServer(("127.0.0.1", 0), server.handler_for(self.feed))
        self.thread = threading.Thread(target=self.http.serve_forever, daemon=True)
        self.thread.start()
        self.url = "http://127.0.0.1:" + str(self.http.server_port)

    def tearDown(self):
        self.http.shutdown()
        self.http.server_close()
        self.thread.join()
        self.temp.cleanup()

    def get(self, path):
        with urlopen(self.url+path, timeout=10) as response:
            return response.read(), response.headers

    def test_archive_index_and_downloads_without_paths_or_state_changes(self):
        rows = json.loads(self.get("/api/archive")[0])["days"]
        self.assertEqual(rows[0]["date"], "2026-01-02")
        before = self.feed.bundle
        data, headers = self.get(rows[0]["download_url"])
        self.assertTrue(data.startswith(b"PK"))
        self.assertIn("attachment", headers["Content-Disposition"])
        saved = json.loads(self.get(rows[0]["state_url"])[0])
        self.assertEqual(saved["revision"], before["state"]["revision"])
        self.assertIs(self.feed.bundle, before)
        self.assertTrue(json.loads(self.get("/api/state")[0])["preview_only"])

    def test_battery_history_csv_and_invalid_query_bounds(self):
        self.feed.history.add_battery(dict(battery_percent=60, battery_voltage=3.85, charging="not_charging"), datetime.now(timezone.utc))
        result = json.loads(self.get("/api/battery?days=7")[0])
        self.assertEqual(len(result["readings"]), 1)
        self.assertFalse(result["estimate"]["available"])
        self.assertIn(b"60,3.85,not_charging", self.get("/api/battery.csv?days=30")[0])
        for path in ("/api/battery?days=365", "/api/battery?days=7&days=30", "/api/battery?days=x",
                     "/api/birds?species=Bird&date=../../private", "/api/birds?species="+"x"*201,
                     "/api/birds?species=Bird&source=invalid", "/api/birds?species=Bird&source=current&source=archive"):
            with self.subTest(path=path), self.assertRaises(HTTPError) as error:
                self.get(path)
            self.assertEqual(error.exception.code, 400)
            error.exception.close()

    def test_bird_details_are_demo_safe_and_head_or_browser_gets_do_not_log_battery(self):
        with patch.object(server.bird_details, "fetch") as upstream:
            data = json.loads(self.get("/api/birds?species=Example+bird&date=2026-01-02")[0])
        upstream.assert_not_called()
        self.assertTrue(data["demo"])
        headers = {"X-FederFunk-Battery-Percent": "50", "X-FederFunk-Battery-Voltage": "3.8",
                   "X-FederFunk-Charging": "not_charging"}
        for method in ("GET", "HEAD"):
            with urlopen(Request(self.url+"/api/v1/display/current_large.bin", headers=headers, method=method), timeout=10):
                pass
        self.assertEqual(self.feed.history.battery(), [])

    def test_bird_details_archive_source_and_missing_archive(self):
        with patch.object(self.feed, "details", return_value={"source": "archive"}) as details:
            data = json.loads(self.get("/api/birds?species=Example+bird&date=2026-01-02&source=archive")[0])
        details.assert_called_once_with("Example bird", "2026-01-02", "archive")
        self.assertEqual(data["source"], "archive")
        with patch.object(server.bird_details, "fetch") as upstream, self.assertRaises(HTTPError) as error:
            self.get("/api/birds?species=Example+bird&date=2026-01-01&source=archive")
        self.assertEqual(error.exception.code, 404)
        error.exception.close()
        upstream.assert_not_called()


if __name__ == "__main__":
    unittest.main()
