"""Daily choices, bird-only scaling, and canvas hit targets."""
from datetime import date, datetime, timezone
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image, ImageDraw
import render


class ArtworkSelectionTests(unittest.TestCase):
    def setUp(self):
        self.settings = {**render.DEFAULTS, "show_qr": False, "show_title": False}
        self.assets = [dict(id=f"variant-{i}", scientific_name="Example bird") for i in range(5)]
        self.birds = [dict(scientific_name="Example bird", common_name="Example", count=5)]

    def test_daily_choice_is_stable_order_independent_and_manual_wins(self):
        day = "2026-10-14"
        chosen = render.choose_birds(self.birds, self.settings, self.assets, date=day)[0][1]
        reordered = render.choose_birds(self.birds, self.settings, list(reversed(self.assets)), date=day)[0][1]
        self.assertEqual(chosen, reordered)
        seen = {render.choose_birds(self.birds, self.settings, self.assets,
                                    date=date(2026, 10, n))[0][1]["id"] for n in range(1, 29)}
        self.assertGreater(len(seen), 1)
        self.settings["variants"] = {"Example bird": "variant-3"}
        for day in ("2026-10-14", "2026-10-15"):
            self.assertEqual(render.choose_birds(self.birds, self.settings, self.assets, date=day)[0][1]["id"],
                             "variant-3")

    def test_fixed_choice_preserves_curated_order_and_mode_is_validated(self):
        settings = {**self.settings, "variant_mode": "fixed"}
        self.assertEqual(render.choose_birds(self.birds, settings, self.assets, date="2026-10-14")[0][1],
                         self.assets[0])
        with self.assertRaises(ValueError):
            render.validate_settings({"variant_mode": "random-every-refresh"}, {}, self.assets)
        self.assertEqual(render.observation_day(date(2026, 10, 14), self.settings), date(2026, 10, 14))
        self.assertEqual(render.observation_day(datetime(2026, 10, 15, 1, tzinfo=timezone.utc), self.settings),
                         date(2026, 10, 14))

    def test_reviewed_subject_area_preserves_frequency_ratio_excluding_perches(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            assets, birds = [], []
            for n, branch_width in enumerate((90, 180)):
                picture = Image.new("RGBA", (220, 160))
                draw = ImageDraw.Draw(picture)
                draw.rectangle((30, 20, 89, 99), fill=(160, 65, 45, 255))
                draw.rectangle((20, 100, 20+branch_width, 110), fill=(90, 60, 20, 255))
                picture.save(root / f"bird-{n}.png")
                assets.append(dict(id=f"bird-{n}", file=f"bird-{n}.png", scientific_name=f"Bird {n}",
                                   sha256=hashlib.sha256((root / f"bird-{n}.png").read_bytes()).hexdigest()))
                birds.append(dict(scientific_name=f"Bird {n}", common_name=f"Bird {n}", count=(4, 1)[n]))
            for style in ("collage", "field_guide"):
                settings = {**self.settings, "style": style, "scale": "linear"}
                with patch("subject_geometry.subject_area", return_value=60*80) as area, \
                     patch("subject_geometry.subject_bounds", return_value=(30, 20, 90, 100)):
                    canvas, _ = render.compose(birds, settings, assets, root, date="2026-10-14")
                # Geometry receives full source pixels, before alpha trimming.
                self.assertEqual([call.args[1].size for call in area.call_args_list], [(220, 160)]*2)
                targets = canvas.info["Bird hotspots"]
                self.assertEqual(len(targets), 2)
                self.assertAlmostEqual(targets[0]["subject"]["width"] / targets[1]["subject"]["width"], 2, delta=.03)
                self.assertEqual(canvas.info["Canvas size"], {"width": 1200, "height": 1600})
                for target in targets:
                    self.assertGreaterEqual(target["x"], 0)
                    self.assertGreaterEqual(target["y"], 0)
                    self.assertLessEqual(target["x"]+target["width"], 1200)
                    self.assertLessEqual(target["y"]+target["height"], 1600)
                    mask = target["alpha_mask"]
                    self.assertLessEqual(max(mask["width"], mask["height"]), 64)
                    self.assertEqual(len(mask["rows"]), mask["height"])
                    self.assertTrue(all(len(row) == mask["width"] for row in mask["rows"]))
                    # Transparent corners must not become clickable.
                    self.assertIn("0", "".join(mask["rows"]))
                    self.assertIn("1", "".join(mask["rows"]))
                    subject = target["subject"]
                    self.assertGreaterEqual(subject["x"], target["x"])
                    self.assertGreaterEqual(subject["y"], target["y"])
                    self.assertLessEqual(subject["x"]+subject["width"], target["x"]+target["width"])


if __name__ == "__main__":
    unittest.main()
