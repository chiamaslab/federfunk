import json
import unittest
from unittest.mock import patch

import bird_details


class BirdDetailsTests(unittest.TestCase):
    def setUp(self):
        self.summary = [{"scientific_name": "Example bird", "common_name": "Visitor", "count": 123,
                         "first_heard": "2026-01-01 08:00:00", "last_heard": "2026-01-02 09:00:00",
                         "avg_confidence": .85, "max_confidence": .99, "private": "not exposed"}]
        self.record = dict(id=123, date="2026-01-02", time="09:00:00", timestamp="2026-01-02T09:00:00Z",
                           scientificName="Example bird", confidence=.91, verified="unverified",
                           source={"url": "rtsp://secret:password@source"}, clipName="/private/recording.wav")
        self.response = dict(data=[self.record], total=50)

    def details(self, **kwargs):
        return bird_details.details("http://127.0.0.1:8080", "http://192.0.2.10:8001/",
                                    "Example bird", "2026-01-02", **kwargs)

    def test_exact_routes_allowlist_real_detail_links_and_separate_counts(self):
        snapshot = {"species": [{"scientific_name": "Example bird", "count": 40, "hourly_counts": [0]*24}]}
        with patch.object(bird_details, "fetch", side_effect=[self.summary, self.response]) as fetch:
            result = self.details(snapshot=snapshot)
        self.assertIn("species=Example+bird&date=2026-01-02&limit=30&offset=0", fetch.call_args.args[1])
        self.assertEqual(result["lifetime"]["count"], 123)
        self.assertEqual(result["daily"]["count"], 50)
        self.assertEqual(result["daily"]["print_count"], 40)
        self.assertTrue(result["limited"])
        self.assertEqual(result["detections"][0]["detail_url"], "http://192.0.2.10:8080/ui/detections/123")
        encoded = json.dumps(result)
        for secret in ("password", "clipName", "/private", "not exposed", "rtsp"):
            self.assertNotIn(secret, encoded)

    def test_wrong_species_date_malformed_counts_or_unbounded_records_fail(self):
        for response in ({**self.response, "total": True}, {**self.response, "data": [self.record]*31},
                         {**self.response, "data": [{**self.record, "scientificName": "Other bird"}]},
                         {**self.response, "data": [{**self.record, "date": "2026-01-01"}]},
                         {**self.response, "data": [{**self.record, "confidence": float("nan")}]},
                         {**self.response, "data": [{**self.record, "id": "../../secret"}]}):
            with patch.object(bird_details, "fetch", side_effect=[self.summary, response]), self.assertRaises(ValueError):
                self.details()

    def test_demo_has_no_live_request_or_invented_lifetime_and_missing_species_stays_empty(self):
        with patch.object(bird_details, "fetch") as fetch:
            result = self.details(demo=True)
        fetch.assert_not_called()
        self.assertIsNone(result["lifetime"])
        self.assertIsNone(result["birdnet_url"])
        with patch.object(bird_details, "fetch", side_effect=[[], {"data": [], "total": 0}]):
            result = self.details()
        self.assertIsNone(result["lifetime"])
        self.assertEqual(result["detections"], [])


if __name__ == "__main__":
    unittest.main()
