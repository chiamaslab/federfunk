"""Draft layout cards use real cached observations without publishing a print."""
import base64
from contextlib import ExitStack
import copy
import hashlib
from http.server import ThreadingHTTPServer
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from PIL import Image, ImageDraw

import classic
import render
import server


class PresentationPreviewTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.assets, self.birds = [], []
        for index in range(3):
            image = Image.new("RGBA", (80 + 20 * index, 100))
            ImageDraw.Draw(image).ellipse((3, 3, image.width-3, 96), fill=(45, 60+40*index, 70, 255))
            path = self.root / f"bird-{index}.png"
            image.save(path)
            self.assets.append(dict(id=f"art-{index}", scientific_name=f"Species {index}",
                                    common_name=f"Bird {index}", file=path.name,
                                    sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                                    title="Historic drawing", rights="Public domain"))
            self.birds.append(dict(scientific_name=f"Species {index}", common_name=f"Bird {index}",
                                   count=20-index*6, latest_heard=f"08:{index:02}:00",
                                   max_confidence=.99, hourly_counts=[]))
        self.feed = server.Feed("http://unused.invalid", self.root / "state",
                                assets=self.assets, artwork_dir=self.root)
        self.feed.settings = render.validate_settings(
            {"schedule": {"enabled": False}, "classic_media": "drawings"}, self.feed.settings, self.assets)
        self.feed.bundle = self.feed.build(self.birds, "2026-01-02", self.feed.settings, "2026-01-02T12:00:00-07:00")
        server.save_bundle(self.feed.directory, self.feed.bundle)
        self.feed.archive(self.feed.bundle)
        blocker = patch.object(classic, "_fetch", side_effect=AssertionError("Tests must not access the network"))
        blocker.start()
        self.addCleanup(blocker.stop)
        classic._cached_photo.cache_clear()
        self.addCleanup(classic._cached_photo.cache_clear)

    def thumbnail(self, row):
        self.assertTrue(row["image_url"].startswith("data:image/png;base64,"))
        return Image.open(io.BytesIO(base64.b64decode(row["image_url"].split(",", 1)[1])))

    def files(self):
        return {str(path.relative_to(self.feed.directory)): path.read_bytes()
                for path in self.feed.directory.rglob("*") if path.is_file()}

    def test_real_layouts_and_draft_toggles_match_production_without_any_write_or_pack(self):
        original, settings, device, files = self.feed.bundle, copy.deepcopy(self.feed.settings), copy.deepcopy(self.feed.device), self.files()
        draft = dict(show_names=True, show_scientific=True, show_counts=True, show_title=False,
                     show_qr=False, show_all=False, max_birds=2, scale="equal", orientation="landscape")
        with ExitStack() as stack:
            for owner, name in ((server, "save_bundle"), (server, "save_device"), (server, "get_json"),
                                (render, "pack"), (self.feed, "refresh"), (self.feed, "archive"),
                                (self.feed, "record_device"), (self.feed, "schedule")):
                stack.enter_context(patch.object(owner, name, side_effect=AssertionError(f"Preview called {name}")))
            result = self.feed.presentation_previews(draft)
        self.assertEqual(result["date"], "2026-01-02")
        self.assertEqual(result["observed_at"], "2026-01-02T12:00:00-07:00")
        self.assertEqual(result["revision"], original["state"]["revision"])
        self.assertEqual([row["style"] for row in result["previews"]], ["collage", "classic", "field_guide"])
        for row in result["previews"]:
            self.assertIsNone(row["error"])
            actual = self.thumbnail(row)
            self.assertEqual(actual.size, (240, 180))
            complete = render.validate_settings({**draft, "style": row["style"]}, settings, self.assets)
            canvas, _, _, _ = self.feed._compose(self.birds, result["date"], complete)
            self.assertEqual(actual.tobytes(), canvas.resize(actual.size, Image.Resampling.LANCZOS).tobytes())
        self.assertIs(self.feed.bundle, original)
        self.assertEqual(self.feed.settings, settings)
        self.assertEqual(self.feed.device, device)
        self.assertEqual(self.files(), files)

    def test_cache_invalidates_on_counts_even_when_live_frame_revision_is_unchanged(self):
        with patch.object(self.feed, "_compose", wraps=self.feed._compose) as compose:
            first = self.feed.presentation_previews({"show_counts": True})
            second = self.feed.presentation_previews({"show_counts": True, "style": "classic"})
            self.assertEqual(compose.call_count, 3)
            self.assertEqual(first["draft_id"], second["draft_id"])
            self.feed.bundle["state"]["stats"]["species"][0]["count"] += 100
            changed = self.feed.presentation_previews({"show_counts": True})
            self.assertEqual(compose.call_count, 6)
            self.assertEqual(first["revision"], changed["revision"])
            self.assertNotEqual(first["draft_id"], changed["draft_id"])
            self.assertNotEqual(first["previews"][0]["image_url"], changed["previews"][0]["image_url"])
            for index in range(10):
                self.feed.presentation_previews({"show_all": False, "max_birds": index+1})
            self.assertEqual(len(self.feed.presentation_cache), 8)

    def test_photo_success_reuses_normal_photo_cache_across_different_toggles(self):
        image = io.BytesIO()
        Image.new("RGB", (200, 100), "blue").save(image, "PNG")
        info = json.dumps(dict(authorName="Example", licenseName="CC BY 4.0",
                               licenseURL="https://creativecommons.org/licenses/by/4.0/")).encode()
        with patch.object(classic, "_fetch", side_effect=[info, image.getvalue()]) as fetch:
            first = self.feed.presentation_previews({"classic_media": "photos"})
            second = self.feed.presentation_previews({"classic_media": "photos", "show_names": True})
            third = self.feed.presentation_previews({"classic_media": "photos", "show_counts": True})
        self.assertEqual(fetch.call_count, 2)  # Metadata plus one photo, not per toggle.
        self.assertTrue(all(result["previews"][1]["error"] is None for result in (first, second, third)))
        credits = first["previews"][1]["credits"]
        self.assertEqual(credits[0]["creator"], "Example")
        self.assertEqual(credits[0]["rights"], "CC BY 4.0")
        self.assertEqual(credits[0]["scientific_name"], "Species 2")
        self.assertEqual(self.thumbnail(first["previews"][1]).size, (240, 320))
        self.assertNotEqual(first["previews"][1]["image_url"], second["previews"][1]["image_url"])

    def test_exact_variant_and_public_credits_without_exposing_external_asset_paths(self):
        path = self.root / "alternate.png"
        Image.new("RGBA", (100, 100), (190, 20, 140, 255)).save(path)
        alternate = {**self.assets[2], "id": "alternate-2", "file": path.name,
                     "_resolved_path": str(path), "source_file": "/private/source-scan.jpg",
                     "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
        self.assets.append(alternate)
        result = self.feed.presentation_previews({"variants": {"Species 2": "alternate-2"}, "show_qr": False})
        for row in result["previews"]:
            species_credits = [credit for credit in row["credits"] if credit["scientific_name"] == "Species 2"]
            self.assertEqual([credit["id"] for credit in species_credits], ["alternate-2"])
            self.assertNotIn("_resolved_path", json.dumps(row["credits"]))
            self.assertNotIn("/private/", json.dumps(row["credits"]))
            self.assertNotIn(str(self.root), json.dumps(self.thumbnail(row).info))
        self.assertEqual(self.thumbnail(result["previews"][1]).getpixel((120, 106)), (190, 20, 140))

    def test_missing_featured_drawing_and_empty_day_do_not_substitute_another_bird(self):
        self.feed.bundle["state"]["stats"]["species"].append(
            {**self.birds[0], "scientific_name": "Unillustrated visitor", "latest_heard": "23:00:00"})
        with patch.object(classic, "compose", wraps=classic.compose) as compose:
            result = self.feed.presentation_previews({})
        self.assertIsNone(compose.call_args.args[2])
        self.assertEqual(compose.call_args.args[5]["scientific_name"], "Unillustrated visitor")
        self.assertEqual(result["previews"][1]["credits"], [])
        self.feed.bundle["state"]["stats"]["species"] = []
        with patch.object(classic, "photo", side_effect=AssertionError("An empty day must not fetch a photo")):
            result = self.feed.presentation_previews({"classic_media": "photos"})
        for row in result["previews"]:
            self.assertEqual(row["credits"], [])
            self.assertIsNone(row["error"])
            self.assertEqual(self.thumbnail(row).size, (240, 320))

    def test_photo_failure_is_isolated_sanitized_and_recovers_after_cooldown_or_revision(self):
        with patch.object(server.time, "monotonic", return_value=100), \
                patch.object(classic, "photo", side_effect=OSError("private-password/path")) as photo:
            first = self.feed.presentation_previews({"classic_media": "photos"})
            self.feed.presentation_previews({"classic_media": "photos", "show_counts": True})
            self.assertEqual(photo.call_count, 1)
        self.assertNotIn("private-password", json.dumps(first))
        self.assertIsNotNone(first["previews"][1]["error"])
        self.assertIsNone(first["previews"][0]["error"])
        self.assertIsNone(first["previews"][2]["error"])
        self.assertIsNotNone(first["previews"][1]["image_url"])  # Actual unavailable-photo layout.
        with patch.object(server.time, "monotonic", return_value=161), \
                patch.object(classic, "photo", return_value=(Image.new("RGB", (100, 100), "blue"), {})) as photo:
            recovered = self.feed.presentation_previews({"classic_media": "photos"})
            photo.assert_called_once()
        self.assertIsNone(recovered["previews"][1]["error"])
        self.feed.bundle["state"]["revision"] = "changed"
        with patch.object(server.time, "monotonic", return_value=120), \
                patch.object(classic, "photo", return_value=(Image.new("RGB", (100, 100), "blue"), {})) as photo:
            result = self.feed.presentation_previews({"classic_media": "photos", "show_counts": True})
            photo.assert_called_once()
        self.assertIsNone(result["previews"][1]["error"])

    def test_overnight_preview_uses_frozen_date_and_does_not_apply_pending_settings(self):
        frozen = self.feed.bundle
        self.feed.settings = render.validate_settings({"show_title": False}, self.feed.settings, self.assets)
        self.feed.resume_pending = True
        with patch.object(self.feed, "schedule", side_effect=AssertionError("Preview must not change schedule state")):
            result = self.feed.presentation_previews({"show_counts": True})
        self.assertEqual(result["date"], frozen["state"]["stats"]["date"])
        self.assertIs(self.feed.bundle, frozen)
        self.assertFalse(self.feed.settings["show_counts"])
        self.assertTrue(self.feed.resume_pending)
        self.assertTrue(frozen["state"]["display_settings"]["show_title"])

    def test_rendering_does_not_hold_live_feed_lock(self):
        entered, release = threading.Event(), threading.Event()
        original = self.feed._compose

        def wait_then_compose(*args, **kwargs):
            entered.set()
            self.assertTrue(release.wait(5))
            return original(*args, **kwargs)

        errors = []

        def request():
            try:
                self.feed.presentation_previews({})
            except Exception as error:
                errors.append(error)

        with patch.object(self.feed, "_compose", side_effect=wait_then_compose):
            worker = threading.Thread(target=request)
            worker.start()
            try:
                self.assertTrue(entered.wait(5))
                acquired = self.feed.lock.acquire(timeout=.5)
                self.assertTrue(acquired)
                if acquired:
                    self.feed.lock.release()
            finally:
                release.set()
                worker.join(10)
        self.assertFalse(worker.is_alive())
        self.assertEqual(errors, [])

    def test_http_security_bounds_and_device_headers_cannot_turn_preview_into_telemetry(self):
        http = ThreadingHTTPServer(("127.0.0.1", 0), server.handler_for(self.feed))
        worker = threading.Thread(target=http.serve_forever, daemon=True)
        worker.start()
        self.addCleanup(http.server_close)
        self.addCleanup(http.shutdown)
        base = f"http://127.0.0.1:{http.server_port}"
        headers = {"Content-Type": "application/json", "X-CSRF-Token": self.feed.csrf_token}
        path = base + "/api/presentation-previews"
        for extra, body, status in (({"X-CSRF-Token": "wrong"}, b"{}", 403),
                                     ({"Origin": "http://evil.invalid"}, b"{}", 403),
                                     ({"Sec-Fetch-Site": "cross-site"}, b"{}", 403),
                                     ({"Host": "evil.invalid"}, b"{}", 403),
                                     ({}, b'{"show_counts":"false"}', 400),
                                     ({}, b'{"private_password":"secret"}', 400),
                                     ({}, b"{"+b" "*16384, 400)):
            with self.subTest(extra=extra, status=status), self.assertRaises(HTTPError) as caught:
                urlopen(Request(path, data=body, headers={**headers, **extra}), timeout=10)
            self.assertEqual(caught.exception.code, status)
            self.assertNotIn(b"secret", caught.exception.read())
            caught.exception.close()
        files, device = self.files(), copy.deepcopy(self.feed.device)
        fake_telemetry = {"User-Agent": "ESP32HTTPClient", "X-FederFunk-Battery-Percent": "50",
                          "X-FederFunk-Battery-Voltage": "3.8", "X-FederFunk-Charging": "not_charging"}
        with urlopen(Request(path, data=b'{"show_names":true}', headers={**headers, **fake_telemetry}), timeout=10) as response:
            result = json.load(response)
            self.assertEqual(response.headers["Cache-Control"], "no-store")
            self.assertEqual(len(result["previews"]), 3)
        self.assertEqual(self.files(), files)
        self.assertEqual(self.feed.device, device)
        self.assertFalse(self.feed.settings["show_names"])
        self.feed.bundle = None
        with self.assertRaises(HTTPError) as caught:
            urlopen(Request(path, data=b"{}", headers=headers), timeout=10)
        self.assertEqual(caught.exception.code, 503)
        caught.exception.close()

    def test_concurrent_http_render_rejects_queue_but_still_serves_cached_drafts(self):
        cached = self.feed.presentation_previews({})
        original, original_bundle = self.feed._compose, self.feed.bundle
        entered, release = threading.Event(), threading.Event()
        results, errors = [], []
        http = ThreadingHTTPServer(("127.0.0.1", 0), server.handler_for(self.feed))
        threading.Thread(target=http.serve_forever, daemon=True).start()
        self.addCleanup(http.server_close)
        self.addCleanup(http.shutdown)
        path = f"http://127.0.0.1:{http.server_port}/api/presentation-previews"
        headers = {"Content-Type": "application/json", "X-CSRF-Token": self.feed.csrf_token}

        def post(draft):
            with urlopen(Request(path, data=json.dumps(draft).encode(), headers=headers), timeout=10) as response:
                return json.load(response)

        def wait_then_compose(*args, **kwargs):
            entered.set()
            self.assertTrue(release.wait(5))
            return original(*args, **kwargs)

        def first_request():
            try:
                results.append(post({"show_counts": True}))
            except Exception as error:
                errors.append(error)

        with patch.object(self.feed, "_compose", side_effect=wait_then_compose):
            worker = threading.Thread(target=first_request)
            worker.start()
            try:
                self.assertTrue(entered.wait(5))
                self.assertEqual(post({})["draft_id"], cached["draft_id"])
                with self.assertRaises(HTTPError) as caught:
                    post({"show_names": True})
                self.assertEqual(caught.exception.code, 503)
                self.assertEqual(caught.exception.headers["Retry-After"], "1")
                self.assertIn("busy", json.load(caught.exception)["error"])
                caught.exception.close()
                self.assertTrue(self.feed.presentation_lock.locked())
            finally:
                release.set()
                worker.join(10)
        self.assertFalse(worker.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(len(results), 1)
        self.assertFalse(self.feed.presentation_lock.locked())
        self.assertEqual(len(post({"show_names": True})["previews"]), 3)
        self.assertIs(self.feed.bundle, original_bundle)
        self.assertFalse(self.feed.settings["show_counts"])

    def test_unexpected_render_error_releases_admission_lock(self):
        with patch.object(self.feed, "_compose", side_effect=AssertionError("unexpected renderer failure")):
            with self.assertRaises(AssertionError):
                self.feed.presentation_previews({})
        self.assertFalse(self.feed.presentation_lock.locked())
        self.assertEqual(len(self.feed.presentation_previews({})["previews"]), 3)


if __name__ == "__main__":
    unittest.main()
