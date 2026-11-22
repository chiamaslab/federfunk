"""Bat identity, provenance, cache integrity and real renderer integration."""
import copy
import hashlib
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
import render
from server import validate_frame


class BatPackTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.cache = self.root / "cache"
        self.pack = json.loads(packs.BATS_REGISTRY.read_text())
        self.metadata = self.root / "metadata"
        self.metadata.mkdir()
        for entry in self.pack["assets"]:
            destination = self.metadata / entry["prepared_file"]
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(packs.BATS_REGISTRY.parent / entry["prepared_file"], destination)
        self.registry = self.metadata / "salt-lake-bats-reviewed.json"
        self.save_registry()
        for context in (patch.object(packs, "registry", return_value=[]),
                        patch.object(packs, "AUDUBON_REGISTRY", self.metadata / "no-audubon.json"),
                        patch.object(packs, "HABIRD_REGISTRY", self.metadata / "no-flight.json"),
                        patch.object(packs, "BATS_REGISTRY", self.registry)):
            context.start()
            self.addCleanup(context.stop)

    def save_registry(self):
        self.registry.write_text(json.dumps(self.pack))

    def install(self):
        return packs.install_pack(self.pack["id"], self.cache)

    def test_reviewed_sources_and_exact_species_are_retained_without_downloads(self):
        with patch.object(packs.urllib.request.OpenerDirector, "open", side_effect=AssertionError("No network")):
            self.assertEqual(packs.load_pack_assets(self.cache), [])
            self.assertFalse(self.cache.exists())
            self.assertEqual(self.install(), 4)
        installed = packs.load_pack_assets(self.cache)
        self.assertEqual({a["scientific_name"] for a in installed}, {
            "Myotis lucifugus", "Lasiurus cinereus", "Eptesicus fuscus", "Tadarida brasiliensis"})
        for asset, entry in zip(installed, self.pack["assets"]):
            self.assertEqual(asset["source_sha256"], entry["review"]["reviewed_source_sha256"])
            self.assertIn("AI-assisted", asset["changes"])
            self.assertTrue(asset["edit_prompt"] and asset["taxonomy_evidence_url"])
            with Image.open(packs.asset_path(asset, self.root)) as image:
                self.assertEqual(hashlib.sha256(image.tobytes()).hexdigest(), entry["pixel_sha256"])
                self.assertEqual(image.getchannel("A").getextrema(), (0, 255))
                self.assertIn("AI-assisted", image.info["changes"])
                self.assertEqual(image.info["source_url"], entry["source_url"])
            self.assertNotIn(str(self.root), json.dumps(packs.public_asset(asset)))
        self.assertEqual(packs.packs_status(self.cache)[0]["status"], "installed")

    def test_corrupt_local_file_cannot_replace_existing_cache(self):
        self.install()
        installed = packs.load_pack_assets(self.cache)
        before = {a["id"]: packs.asset_path(a, self.root).read_bytes() for a in installed}
        # Check every file before publishing; a later invalid member must not
        # partly rewrite an already installed collection.
        (self.metadata / self.pack["assets"][-1]["prepared_file"]).write_bytes(b"damaged")
        with self.assertRaisesRegex(ValueError, "review"):
            self.install()
        self.assertEqual(before, {a["id"]: packs.asset_path(a, self.root).read_bytes() for a in installed})

    def test_changed_review_revision_pixels_and_traversal_are_rejected(self):
        original = copy.deepcopy(self.pack)
        for alteration, error in (({"prepared_file": "edits/bats/../../../outside.png"}, "Unsafe|escapes"),
                                  ({"pixel_sha256": "0" * 64}, "review"),
                                  ({"review": {"status": "pending"}}, "review")):
            self.pack = copy.deepcopy(original)
            self.pack["assets"][0].update(alteration)
            self.save_registry()
            with self.assertRaisesRegex(ValueError, error):
                self.install()
        self.pack = copy.deepcopy(original)
        self.pack["revision"] = "0" * 64
        self.save_registry()
        with self.assertRaisesRegex(ValueError, "revision"):
            self.install()

    def test_damaged_cached_bat_is_excluded(self):
        self.install()
        asset = packs.load_pack_assets(self.cache)[0]
        packs.asset_path(asset, self.root).write_bytes(b"damaged")
        remaining = packs.load_pack_assets(self.cache)
        self.assertEqual(len(remaining), 3)
        self.assertNotIn(asset["id"], {a["id"] for a in remaining})

    def test_bats_render_with_daily_mirrors_and_no_substitute_species(self):
        self.install()
        assets = packs.load_pack_assets(self.cache)
        birds = [dict(scientific_name=a["scientific_name"], common_name=a["common_name"], count=1) for a in assets]
        birds.append(dict(scientific_name="Myotis volans", common_name="Long-legged Myotis", count=1))
        settings = {**render.DEFAULTS, "show_qr": False, "show_names": True, "show_title": False,
                    "scale": "equal", "mirror_mode": "daily"}
        selected = render.choose_birds(birds, settings, assets, date="2026-11-08")
        self.assertEqual(len(selected), 4)
        for bird, asset in selected:
            self.assertEqual(bird["scientific_name"], asset["scientific_name"])
        for style in ("collage", "field_guide"):
            canvas, credits = render.compose(birds, {**settings, "style": style}, assets, date="2026-11-08")
            self.assertEqual(len(credits), 4)
            self.assertEqual(len(canvas.info["Bird hotspots"]), 4)
            validate_frame(render.pack(canvas)[0])


if __name__ == "__main__":
    unittest.main()
