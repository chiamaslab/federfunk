"""Held device prints never freeze stats, lose birds, or corrupt daily history."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import meaningful_updates as updates
import render
import server


def settings(**patches):
    return render.validate_settings({"update_mode": "meaningful", "scale": "linear", "schedule": {"enabled": False}, **patches}, {}, [])


def state(counts, stamp="2026-10-26T12:00:00+00:00", config=None):
    birds = [dict(scientific_name=name, common_name=name + " bird", count=count, max_confidence=.99,
                  displayed=True, artwork_id=name + "-art") for name, count in counts.items()]
    stats = dict(date=stamp[:10], observed_at=stamp, species=birds, species_count=len(birds), total_detections=sum(counts.values()))
    return dict(stats=stats, display_stats=deepcopy(stats), print_generated_at=stamp, artwork_deferred=False,
                settings=config or settings(), display_settings=config or settings(), revision="old", demo=False)


def bundle(snapshot, value=0x11):
    frame = bytes([value]) * 960000
    snapshot = {**snapshot, "revision": hashlib.sha256(frame).hexdigest()}
    return dict(state=snapshot, frame=frame, art=b"art" + bytes([value]), preview=b"preview" + bytes([value]))


class MeaningfulPolicyTests(unittest.TestCase):
    def test_disabled_and_first_print_publish(self):
        old, new = state({"A": 100}), state({"A": 101})
        self.assertEqual(updates.publish_reason(old, new, settings(update_mode="every_check")), "every_check")
        self.assertEqual(updates.publish_reason(None, new, settings()), "first_print")
        self.assertIsNone(updates.publish_reason(old, new, settings()))  # A single bird's area is unchanged.

    def test_cumulative_relative_area_uses_published_baseline(self):
        original = bundle(state({"A": 100, "B": 100}))
        first = bundle(state({"A": 110, "B": 100}, "2026-10-26T12:10:00+00:00"), 0x22)
        self.assertIsNone(updates.publish_reason(original["state"], first["state"], settings()))
        retained = updates.retain_print(original, first)
        next_candidate = state({"A": 125, "B": 100}, "2026-10-26T12:20:00+00:00")
        self.assertEqual(updates.publish_reason(retained["state"], next_candidate, settings()), "size")
        self.assertEqual(retained["state"]["display_stats"]["species"][0]["count"], 100)
        self.assertEqual(retained["state"]["stats"]["species"][0]["count"], 110)
        self.assertEqual(retained["state"]["print_generated_at"], original["state"]["print_generated_at"])
        self.assertTrue(retained["state"]["artwork_deferred"])
        for key in ("frame", "art", "preview"):
            self.assertEqual(retained[key], original[key])

    def test_log_equal_and_proportional_area(self):
        for scale, counts, reason in (("log", {"A": 200, "B": 100}, None),
                                     ("log", {"A": 400, "B": 100}, "size"),
                                     ("equal", {"A": 10000, "B": 1}, None),
                                     ("linear", {"A": 200, "B": 200}, None)):
            config = settings(scale=scale)
            self.assertEqual(updates.publish_reason(state({"A": 100, "B": 100}, config=config),
                                                   state(counts, config=config), config), reason)

    def test_new_species_day_artwork_selection_and_settings(self):
        old = state({"A": 100, "B": 100})
        new = state({"A": 100, "B": 100, "C": 1})
        new["stats"]["species"][-1]["displayed"] = False
        self.assertEqual(updates.publish_reason(old, new, settings()), "species")
        self.assertEqual(updates.publish_reason(old, state({"A": 100}, "2026-10-27T12:00:00+00:00"), settings()), "new_day")
        new = deepcopy(old)
        new["stats"]["species"][0]["artwork_id"] = "different"
        self.assertEqual(updates.publish_reason(old, new, settings()), "artwork")
        new["stats"]["species"][0]["displayed"] = False
        self.assertEqual(updates.publish_reason(old, new, settings()), "selection")
        self.assertEqual(updates.publish_reason(old, old, settings(show_names=True)), "settings")
        self.assertIsNone(updates.publish_reason(old, old, settings(stop_voltage=3.35, refresh_interval_minutes=60)))

    def test_visible_counts_and_names_refresh_but_hidden_counts_wait(self):
        config = settings(show_counts=True)
        old, new = state({"A": 100, "B": 100}, config=config), state({"A": 101, "B": 100}, config=config)
        self.assertEqual(updates.publish_reason(old, new, config), "labels")
        config = settings(show_names=True)
        old = state({"A": 100}, config=config)
        new = deepcopy(old)
        new["stats"]["species"][0]["common_name"] = "Corrected name"
        self.assertEqual(updates.publish_reason(old, new, config), "labels")

    def test_max_age_tracks_print_not_last_poll_and_same_pixels_need_no_refresh(self):
        old = bundle(state({"A": 100}))
        new = bundle(state({"A": 100}, "2026-10-26T14:59:00+00:00"))
        self.assertIsNone(updates.publish_reason(old["state"], new["state"], settings()))
        held = updates.retain_print(old, new)
        self.assertFalse(held["state"]["artwork_deferred"])
        last = bundle(state({"A": 100}, "2026-10-26T15:00:00+00:00"))
        self.assertEqual(updates.publish_reason(held["state"], last["state"], settings()), "maximum_age")
        self.assertEqual(render.etag(last["frame"]), render.etag(old["frame"]))

    def test_classic_feature_and_hidden_bar_changes(self):
        config = settings(style="classic")
        old = state({"A": 100, "B": 50}, config=config)
        new = state({"A": 100, "B": 60}, config=config)
        self.assertEqual(updates.publish_reason(old, new, config), "size")
        new = state({"A": 100, "B": 101}, config=config)
        self.assertEqual(updates.publish_reason(old, new, config), "selection")
        new = deepcopy(old)
        new["stats"]["species"][1]["latest_heard"] = "13:00:00"
        self.assertEqual(updates.publish_reason(old, new, config), "selection")


class MeaningfulFeedTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.feed = server.Feed("http://unused.invalid", Path(self.temp.name), assets=[])
        self.feed.settings = settings()
        self.now = datetime.now(timezone.utc).replace(microsecond=0)
        self.original = bundle(state({"A": 100, "B": 100}, self.now.isoformat(), self.feed.settings))
        self.feed.bundle = self.original
        server.save_bundle(self.feed.directory, self.original)
        self.feed.archive(self.original)
        self.old_day = (self.now - timedelta(days=1)).isoformat()
        self.feed.archive(bundle(state({"A": 3}, self.old_day, self.feed.settings)))
        self.old_archive = self.feed.history.archived(self.old_day[:10], "download.zip")

    def refresh_with(self, counts):
        rows = [dict(scientific_name=name, common_name=name, count=count, max_confidence=.99) for name, count in counts.items()]
        def build(birds, day, config, observed_at, zone):
            candidate = state(counts, observed_at, config)
            # Use the queried local day instead of the UTC day at midnight.
            candidate["stats"]["date"] = candidate["display_stats"]["date"] = day
            self.candidate = bundle(candidate, 0x22)
            return self.candidate
        # Control the day so the policy test is independent of test runner time.
        class Clock:
            @staticmethod
            def now(zone=None):
                return self.now + timedelta(minutes=30)
        with patch.object(server, "get_json", return_value=rows), patch.object(self.feed, "build", side_effect=build), \
                patch.object(server, "datetime", Clock), patch.object(self.feed, "schedule", return_value={"active": True, "sleep_seconds": 0}):
            self.feed.refresh()

    def test_fresh_history_held_main_picture_and_restart_keep_cumulative_baseline(self):
        self.refresh_with({"A": 110, "B": 100})
        self.assertEqual(self.feed.bundle["frame"], self.original["frame"])
        self.assertEqual(self.feed.bundle["state"]["stats"]["total_detections"], 210)
        self.assertEqual(self.feed.bundle["state"]["display_stats"]["total_detections"], 200)
        day = self.now.date().isoformat()
        archived = json.loads(self.feed.history.archived(day, "state.json"))
        self.assertEqual(archived["stats"]["total_detections"], 210)
        self.assertEqual(archived["display_stats"], archived["stats"])
        self.assertEqual(self.feed.history.archived(day, "art.png"), self.candidate["art"])
        self.assertEqual(self.feed.history.archived(self.old_day[:10], "download.zip"), self.old_archive)
        self.assertIsNotNone(self.feed.last_success)
        self.feed = server.Feed("http://unused.invalid", self.feed.directory, assets=[])
        self.assertEqual(self.feed.bundle["state"]["display_stats"]["total_detections"], 200)
        self.refresh_with({"A": 125, "B": 100})
        self.assertEqual(self.feed.bundle["frame"], self.candidate["frame"])
        self.assertEqual(self.feed.bundle["state"]["display_stats"]["total_detections"], 225)
        self.assertEqual(self.feed.history.archived(self.old_day[:10], "download.zip"), self.old_archive)

    def test_policy_save_during_hold_never_replaces_fresh_archive_with_held_art(self):
        self.refresh_with({"A": 110, "B": 100})
        day = self.now.date().isoformat()
        archive = self.feed.history.archived(day, "download.zip")
        self.feed.update_settings({"stop_voltage": 3.35, "update_mode": "every_check"})
        self.assertEqual(self.feed.history.archived(day, "download.zip"), archive)
        self.assertEqual(self.feed.bundle["frame"], self.original["frame"])
        self.assertEqual(self.feed.bundle["state"]["display_stats"]["total_detections"], 200)

    def test_scheduled_pause_does_not_fetch_publish_or_archive(self):
        with patch.object(self.feed, "schedule", return_value={"active": False, "sleep_seconds": 36000}), \
                patch.object(server, "get_json", side_effect=AssertionError("Must not fetch during rest")), \
                patch.object(self.feed, "archive", side_effect=AssertionError("Must not rewrite overnight history")):
            self.feed.refresh()
            frame, hold = self.feed.frame_for_display()
        self.assertIs(frame, self.original)
        self.assertEqual(hold, 36000)

    def test_bird_details_print_count_uses_picture_snapshot(self):
        self.refresh_with({"A": 110, "B": 100})
        with patch.object(server.bird_details, "details", return_value={}) as details:
            self.feed.details("A", self.now.date().isoformat())
        self.assertEqual(details.call_args.args[4]["total_detections"], 200)

    def test_today_archive_details_use_archive_snapshot_and_separate_cache(self):
        self.refresh_with({"A": 110, "B": 100})
        day = self.now.date().isoformat()
        archived = json.loads(self.feed.history.archived(day, "state.json"))
        archived["stats"]["species"][0]["artwork_id"] = "archive-art"
        archived["display_stats"]["species"][0]["artwork_id"] = "archive-art"
        archived["artwork_credits"] = [dict(id="archive-art", scientific_name="A", title="Archived drawing")]
        self.feed.archive({**self.candidate, "state": archived})
        def details(base, url, species, date, snapshot, assets, demo):
            return dict(daily={"print_count": snapshot["species"][0]["count"]}, artworks=assets)
        with patch.object(server.bird_details, "details", side_effect=details) as lookup:
            main = self.feed.details("A", day)
            saved = self.feed.details("A", day, source="archive")
            self.assertEqual(main["daily"]["print_count"], 100)
            self.assertEqual(saved["daily"]["print_count"], 110)
            self.assertEqual(saved["print_artwork_id"], "archive-art")
            self.assertEqual(saved["artworks"][0]["title"], "Archived drawing")
            self.assertEqual(self.feed.details("A", day), main)
            self.assertEqual(lookup.call_count, 2)
            # The same pixels can represent later totals when counts are hidden.
            later = deepcopy(archived)
            for key in ("stats", "display_stats"):
                later[key]["species"][0]["count"] = 111
                later[key]["observed_at"] = (datetime.fromisoformat(later[key]["observed_at"]) + timedelta(minutes=5)).isoformat()
            self.feed.archive({**self.candidate, "state": later})
            self.assertEqual(self.feed.details("A", day, source="archive")["daily"]["print_count"], 111)
            self.assertEqual(lookup.call_count, 3)


if __name__ == "__main__":
    unittest.main()
