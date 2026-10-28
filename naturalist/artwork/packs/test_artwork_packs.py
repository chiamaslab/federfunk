import hashlib
import io
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import artwork_packs as packs


class PackTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        image = Image.new("RGBA", (12, 10), (90, 110, 140, 210))
        buffer = io.BytesIO()
        image.save(buffer, format="WEBP", lossless=True)
        self.source = buffer.getvalue()
        decoded = Image.open(io.BytesIO(self.source)).convert("RGBA")
        self.entry = {
            "id": "test-bird", "upstream_file": "assets/artwork/classic/birds/test-bird.webp",
            "source_sha256": hashlib.sha256(self.source).hexdigest(),
            "pixel_sha256": hashlib.sha256(decoded.tobytes()).hexdigest(),
            "width": 12, "height": 10, "review": {"status": "reviewed"},
            "rights": "CC BY-SA 4.0", "rights_url": "https://creativecommons.org/licenses/by-sa/4.0/",
            "source_url": "https://example.org/plate", "attribution": "Artist; prepared by editor",
            "changes": "Lossless decoded RGBA conversion",
        }
        self.pack = {"id": "test", "revision": "a" * 40, "assets": [self.entry], "title": "Test pack",
                     "source_url": "https://example.org/source", "rights": self.entry["rights"],
                     "rights_url": self.entry["rights_url"], "attribution_url": "https://example.org/credits"}
        self.source_root = self.root / "upstream"
        self.source_file = self.source_root / self.entry["upstream_file"]
        self.source_file.parent.mkdir(parents=True)
        self.source_file.write_bytes(self.source)
        self.cache = self.root / "external-cache"
        self.registry_patch = patch.object(packs, "registry", return_value=[self.pack])
        self.registry_patch.start()
        self.addCleanup(self.registry_patch.stop)

    def test_verified_import_preserves_pixels_and_embeds_credits(self):
        with patch.object(packs.urllib.request.OpenerDirector, "open", side_effect=AssertionError("Network not allowed")):
            self.assertEqual(packs.install_pack("test", self.cache, self.source_root), 1)
            asset, = packs.load_pack_assets(self.cache)
        path = packs.asset_path(asset, self.root / "bundled")
        self.assertTrue(path.is_relative_to(self.cache.resolve()))
        self.assertEqual(path.stat().st_mode & 0o777, 0o644)
        self.assertEqual(path.parent.stat().st_mode & 0o777, 0o755)
        with Image.open(path) as image:
            self.assertEqual(image.format, "PNG")
            self.assertEqual(hashlib.sha256(image.convert("RGBA").tobytes()).hexdigest(), self.entry["pixel_sha256"])
            self.assertEqual(image.info["rights_url"], self.entry["rights_url"])
            self.assertEqual(image.info["attribution"], self.entry["attribution"])
        self.assertEqual(packs.packs_status(self.cache)[0]["installed_count"], 1)
        self.assertNotIn(str(self.cache), json.dumps(packs.public_asset(asset)))
        self.assertNotIn("source_file", packs.public_asset({"source_file": "/tmp/private", "source_url": "https://example.org"}))

    def test_corrupt_source_does_not_replace_valid_installed_file(self):
        packs.install_pack("test", self.cache, self.source_root)
        path = packs.asset_path(packs.load_pack_assets(self.cache)[0], self.root)
        before = path.read_bytes()
        self.source_file.write_bytes(b"corrupt image")
        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            packs.install_pack("test", self.cache, self.source_root)
        self.assertEqual(before, path.read_bytes())

    def test_modified_cached_pixels_are_not_selectable(self):
        packs.install_pack("test", self.cache, self.source_root)
        asset = packs.load_pack_assets(self.cache)[0]
        path = packs.asset_path(asset, self.root)
        Image.new("RGBA", (12, 10), "red").save(path)
        self.assertEqual(packs.load_pack_assets(self.cache), [])
        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            packs.asset_path(asset, self.root)

    def test_unknown_unreviewed_oversized_and_repository_cache_rejected(self):
        with self.assertRaisesRegex(ValueError, "Unknown"):
            packs.install_pack("unregistered", self.cache)
        with self.assertRaisesRegex(ValueError, "outside the repository"):
            packs.install_pack("test", Path(packs.__file__).parent / "forbidden-cache", self.source_root)
        self.entry["review"]["status"] = "pending"
        with self.assertRaisesRegex(ValueError, "unreviewed"):
            packs.install_pack("test", self.cache, self.source_root)
        self.entry["review"]["status"] = "reviewed"
        with patch.object(packs, "MAX_BYTES", 2):
            with self.assertRaises(ValueError):
                packs.install_pack("test", self.cache, self.source_root)

    def test_traversal_and_symlinks_cannot_escape_source_or_cache(self):
        self.source_file.unlink()
        outside = self.root / "outside.webp"
        outside.write_bytes(self.source)
        self.source_file.symlink_to(outside)
        with self.assertRaisesRegex(ValueError, "escapes"):
            packs.install_pack("test", self.cache, self.source_root)
        with self.assertRaisesRegex(ValueError, "Unsafe"):
            packs.asset_path({"file": "../outside.webp", "sha256": self.entry["source_sha256"]}, self.source_root)
        self.entry["upstream_file"] = "assets/artwork/classic/birds/../../secret.webp"
        with self.assertRaisesRegex(ValueError, "Unsafe"):
            packs.install_pack("test", self.cache, self.source_root)

    def test_missing_cache_has_no_side_effects_or_downloads(self):
        with patch.object(packs.urllib.request.OpenerDirector, "open", side_effect=AssertionError("Network not allowed")):
            self.assertEqual(packs.load_pack_assets(self.cache), [])
            self.assertEqual(packs.packs_status(self.cache)[0]["status"], "not-installed")
        self.assertFalse(self.cache.exists())


class RegistryTests(unittest.TestCase):
    def test_actual_registry_has_only_pinned_individually_reviewed_assets(self):
        entries = []
        for pack in packs.registry():
            self.assertEqual(len(pack["revision"]), 40)
            for entry in pack["assets"]:
                entries.append(entry["id"])
                for key in ("species_check", "orientation", "crop", "scope", "reviewed_source_sha256", "reviewed_pixel_sha256"):
                    self.assertTrue(entry["review"][key])
                self.assertEqual(entry["review"]["status"], "reviewed")
                self.assertEqual(entry["source_sha256"], entry["review"]["reviewed_source_sha256"])
                self.assertEqual(entry["pixel_sha256"], entry["review"]["reviewed_pixel_sha256"])
                self.assertIn(pack["revision"], entry["upstream_url"])
                self.assertTrue(entry["attribution"] and entry["rights_url"] and entry["changes"])
        self.assertEqual(len(entries), len(set(entries)))
        self.assertGreaterEqual(len(entries), 8)

    def test_north_america_registry_contains_only_single_bird_display_choices(self):
        pack = next(p for p in packs.registry() if p["id"] == "fugleramme-north-america-reviewed")
        self.assertEqual(len(pack["assets"]), 30)
        species = {entry["scientific_name"] for entry in pack["assets"]}
        self.assertEqual(len(species), 30)
        # These available upstream files were deliberately not admitted: the
        # chosen cutout still contains more than one bird or taxonomy is held.
        self.assertTrue({"Zenaida macroura", "Sayornis saya", "Zonotrichia albicollis",
                         "Tyto alba", "Anas crecca", "Setophaga petechia"}.isdisjoint(species))
        for entry in pack["assets"]:
            self.assertIn("exactly one bird", entry["review"]["crop"])
            self.assertEqual(entry["review"]["reviewed_source_sha256"], entry["source_sha256"])
            self.assertEqual(entry["review"]["reviewed_pixel_sha256"], entry["pixel_sha256"])


class SupportRemovalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.record = json.loads(packs.SUPPORT_REMOVALS.read_text())
        self.edit = self.record["assets"][0]
        original = next(entry for pack in packs.registry() for entry in pack["assets"]
                        if entry["id"] == self.edit["id"])
        self.original = {**original, "sha256": "unchanged-source-png", "pack_id": "source-pack"}
        source = packs.SUPPORT_REMOVALS.parent / self.edit["file"]
        target = self.root / self.edit["file"]
        target.parent.mkdir(parents=True)
        shutil.copy2(source, target)
        self.registry = self.root / "support-removals-reviewed.json"
        self.registry.write_text(json.dumps({**self.record, "assets": [self.edit]}))
        self.registry_patch = patch.object(packs, "SUPPORT_REMOVALS", self.registry)
        self.registry_patch.start()
        self.addCleanup(self.registry_patch.stop)

    def test_verified_edit_preserves_selection_and_credit_but_changes_image(self):
        result, = packs._apply_reviewed_support_removals([self.original])
        self.assertEqual(result["id"], self.original["id"])
        self.assertEqual(result["scientific_name"], self.original["scientific_name"])
        self.assertEqual(result["source_url"], self.original["source_url"])
        self.assertEqual(result["rights"], "CC BY-SA 4.0")
        self.assertIn("AI-assisted", result["attribution"])
        self.assertEqual(result["sha256"], self.edit["sha256"])
        self.assertEqual(result["source_pixel_sha256"], self.original["pixel_sha256"])
        self.assertEqual(packs.asset_path(result, self.root), (self.root / self.edit["file"]).resolve())
        self.assertNotIn(str(self.root), json.dumps(packs.public_asset(result)))
        unrelated = {**self.original, "id": "unrelated"}
        self.assertEqual(packs._apply_reviewed_support_removals([unrelated]), [unrelated])

    def test_modified_edit_or_source_cannot_be_selected(self):
        self.assertEqual(packs._apply_reviewed_support_removals([
            {**self.original, "pixel_sha256": "wrong-source-pixels"}]), [])
        target = self.root / self.edit["file"]
        target.write_bytes(b"corrupt png")
        self.assertEqual(packs._apply_reviewed_support_removals([self.original]), [])

    def test_unreviewed_or_escaped_edit_is_rejected(self):
        for change in ({"review": {**self.edit["review"], "status": "pending"}},
                       {"file": "../outside.png"}):
            self.registry.write_text(json.dumps({**self.record, "assets": [{**self.edit, **change}]}))
            self.assertEqual(packs._apply_reviewed_support_removals([self.original]), [])


if __name__ == "__main__":
    unittest.main()
