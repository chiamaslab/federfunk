import copy
import json
from pathlib import Path
import sys
import unittest

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import subject_geometry as subjects


class SubjectTests(unittest.TestCase):
    def setUp(self):
        self.image = Image.new("RGBA", (100, 100), (0, 0, 0, 0))
        draw = ImageDraw.Draw(self.image)
        draw.rectangle((10, 10, 29, 29), fill=(200, 90, 20, 255))
        draw.rectangle((30, 20, 89, 23), fill=(100, 60, 20, 255))
        self.asset = {"id": "test", "sha256": "test-hash", "subject_geometry": {
            "sha256": "test-hash", "size": [100, 100], "review": {"status": "reviewed"},
            "polygons": [[[10 / 99, 10 / 99], [29 / 99, 10 / 99], [29 / 99, 29 / 99], [10 / 99, 29 / 99]]],
        }}

    def test_perch_does_not_change_bird_area_or_pixels(self):
        before = self.image.tobytes()
        self.assertEqual(subjects.subject_area(self.asset, self.image), 400)
        self.assertEqual(subjects.subject_bounds(self.asset, self.image), (10, 10, 30, 30))
        self.assertEqual(self.image.tobytes(), before)
        self.assertTrue(subjects.geometry_status(self.asset, self.image)["reviewed"])

    def test_stale_hash_dimensions_or_missing_geometry_fall_back_explicitly(self):
        for change in ("hash", "size", "missing"):
            asset = copy.deepcopy(self.asset)
            if change == "hash":
                asset["sha256"] = "new-artwork"
            elif change == "size":
                asset["subject_geometry"]["size"] = [50, 50]
            else:
                del asset["subject_geometry"]
            self.assertFalse(subjects.geometry_status(asset, self.image)["reviewed"])
            self.assertEqual(subjects.subject_area(asset, self.image), 640)

    def test_empty_or_invalid_geometry_does_not_hide_artwork(self):
        for polygon in ([[0, 0], [0.01, 0], [0, 0.01]], [[0, 0], [2, 0], [0, 1]]):
            asset = copy.deepcopy(self.asset)
            asset["subject_geometry"]["polygons"] = [polygon]
            self.assertFalse(subjects.geometry_status(asset, self.image)["reviewed"])
            self.assertEqual(subjects.subject_area(asset, self.image), 640)

    def test_partial_alpha_weights_area(self):
        self.image.putalpha(self.image.getchannel("A").point(lambda value: value // 2))
        self.assertAlmostEqual(subjects.subject_area(self.asset, self.image), 400 * 127 / 255)

    def test_all_bundled_artwork_has_hash_bound_visual_review(self):
        root = Path(__file__).resolve().parents[1]
        assets = json.loads((root / "manifest.json").read_text())["assets"]
        geometry = subjects._catalogue()
        self.assertEqual(len(assets), 25)
        for asset in assets:
            with self.subTest(asset=asset["id"]), Image.open(root / asset["file"]) as image:
                self.assertEqual(geometry[asset["id"]]["sha256"], asset["sha256"])
                self.assertTrue(subjects.geometry_status(asset, image)["reviewed"])
                self.assertTrue(geometry[asset["id"]]["review"]["notes"])
                alpha_area = sum(i * n for i, n in enumerate(image.convert("RGBA").getchannel("A").histogram())) / 255
                self.assertGreater(subjects.subject_area(asset, image), alpha_area * 0.70)
                self.assertLessEqual(subjects.subject_area(asset, image), alpha_area)


if __name__ == "__main__":
    unittest.main()
