"""Color experiments must preserve production pixels and the wire protocol."""
import hashlib
import io
import json
import unittest

from PIL import Image, ImageChops
import color_profiles
import render
from test_display_render import qr_canvas


def approved_fixture():
    canvas = Image.new("RGB", (1200, 1600), "white")
    colors = [(0, 0, 0), (255, 255, 0), (255, 0, 0), (0, 0, 255),
              (0, 255, 0), (160, 65, 45), (170, 145, 55), (45, 75, 160),
              (35, 39, 33), (128, 132, 136), (12, 18, 24), (90, 140, 160)]
    for n, color in enumerate(colors):
        x, y = (n % 4)*230+70, (n // 4)*280+110
        canvas.paste(color, (x, y, x+150, y+190))
    return canvas


class ColorProfileTests(unittest.TestCase):
    def test_default_matches_approved_prechange_golden_pixels(self):
        canvas = approved_fixture()
        frame, preview = render.pack(canvas)
        # Captured from the approved renderer before adding profiles. The
        # pixel hash avoids coupling this test to PNG compressor versions.
        self.assertEqual(hashlib.sha256(frame).hexdigest(),
                         "8032ebeb0e331845ca3b05242c1dcaacad923186a432cb7407440f3c340d8503")
        self.assertEqual(hashlib.sha256(Image.open(io.BytesIO(preview)).tobytes()).hexdigest(),
                         "2fa2eadc036d7357675d40647dfb234c83a053f629dca2e44a3a353012357467")
        self.assertEqual(render.pack(canvas, "current"), (frame, preview))

    def test_compensated_neutrals_stay_neutral_and_paper_never_speckles(self):
        for profile in ("compensated_floyd", "compensated_stucki"):
            image = Image.new("RGB", (160, 80), "white")
            image.paste((120, 120, 120), (20, 20, 70, 60))
            image.paste((128, 132, 136), (80, 20, 130, 60))
            before = image.tobytes()
            output = color_profiles.compensated(image, profile)
            self.assertEqual(image.tobytes(), before)
            self.assertEqual(set(output.tobytes()), {0, 1})
            for box in ((0, 0, 160, 20), (0, 60, 160, 80), (70, 0, 80, 80)):
                self.assertEqual(set(output.crop(box).tobytes()), {1})
            color = color_profiles.compensated(Image.new("RGB", (80, 80), (160, 65, 45)), profile)
            self.assertIn(3, color.tobytes())  # red feathers remain red

    def test_every_profile_preserves_native_codes_rotation_and_qr(self):
        palette = [255] * 768
        for code, color in zip((0, 1, 2, 3, 5, 6), render.COLORS):
            palette[code*3:code*3+3] = color
        for size in ((1200, 1600), (1600, 1200)):
            canvas, _ = qr_canvas(size=size)
            canvas.paste((160, 65, 45), (50, 80, 140, 160))
            canvas.paste((45, 75, 160), (size[0]-160, 80, size[0]-50, 160))
            original = canvas.tobytes()
            qr_reference = None
            for profile in color_profiles.PROFILES:
                with self.subTest(size=size, profile=profile):
                    frame, raw = render.pack(canvas, profile)
                    self.assertEqual(len(frame), 960000)
                    codes = bytes(value for packed in frame for value in (packed >> 4, packed & 15))
                    self.assertLessEqual(set(codes), {0, 1, 2, 3, 5, 6})
                    decoded = Image.frombytes("P", (1600, 1200), codes)
                    decoded.putpalette(palette)
                    decoded = decoded.convert("RGB")
                    if size[0] == 1200:
                        decoded = decoded.transpose(Image.Transpose.ROTATE_270)
                    preview = Image.open(io.BytesIO(raw)).convert("RGB")
                    self.assertIsNone(ImageChops.difference(decoded, preview).getbbox())
                    qr = preview.crop(canvas.info["QR bounds"]).tobytes()
                    if qr_reference is None:
                        qr_reference = qr
                    self.assertEqual(qr, qr_reference)
            self.assertEqual(canvas.tobytes(), original)

    def test_profiles_are_explicit_and_metadata_is_serializable(self):
        json.dumps(color_profiles.PROFILES)
        self.assertFalse(color_profiles.PROFILES["current"]["experimental"])
        with self.assertRaises(ValueError):
            render.pack(Image.new("RGB", (1200, 1600)), "unknown")
        with self.assertRaises(ValueError):
            render.validate_settings({"color_profile": "compensated_stucki"}, {}, [])


if __name__ == "__main__":
    unittest.main()
