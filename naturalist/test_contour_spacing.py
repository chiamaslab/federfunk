"""Small spacing repairs preserve birds, their sizes and full edge contours."""
import math
import unittest

from PIL import Image, ImageChops, ImageDraw, ImageFilter

import same_size_collage as layout


def placement(identity, x, y, width=40, height=40, label_width=None, label_height=0):
    image = Image.new('RGBA', (width, height), (30, 50, 20, 255))
    return dict(item=dict(identity=identity, image=image), x=x, y=y, w=width, h=height,
                cx=x+width/2, label_width=label_width or width, label_height=label_height,
                label_size=20, label_line_height=25)


def raster(p, lines, size=(1800, 1800), origin=100):
    """Independent whole-canvas raster; includes padding outside the print."""
    canvas = Image.new('L', size)
    x, y = round(p['x'])+origin, round(p['y'])+origin
    picture = p['item']['image'].resize((p['w'], p['h']), Image.Resampling.LANCZOS)
    canvas.paste(picture.getchannel('A').point(lambda value: 255 if value else 0), (x, y))
    if lines:
        ImageDraw.Draw(canvas).rectangle((math.floor(p['cx']-p['label_width']/2)+origin,
                                         y+p['h']+10,
                                         math.ceil(p['cx']+p['label_width']/2)+origin,
                                         y+p['h']+p['label_height']), fill=255)
    return canvas


class ContourSpacingTests(unittest.TestCase):
    def assert_dimensions(self, before, after):
        self.assertEqual([(p['item']['identity'], p['w'], p['h'], p['label_width'], p['label_height']) for p in before],
                         [(p['item']['identity'], p['w'], p['h'], p['label_width'], p['label_height']) for p in after])

    def assert_gap(self, placements, gap, lines=0):
        # Independently dilate just one full-canvas mask by the whole requested
        # gap, checking against each other native-resolution contour.
        width = max(math.ceil(p['cx']+p['label_width']/2) for p in placements)+200
        height = max(p['y']+p['h']+p['label_height'] for p in placements)+200
        masks = [raster(p, lines, size=(width, height)) for p in placements]
        for i, mask in enumerate(masks):
            dilated = mask.filter(ImageFilter.MaxFilter(2*gap+1))
            for j in range(i):
                self.assertIsNone(ImageChops.multiply(dilated, masks[j]).getbbox(), (i, j))

    def test_two_pixel_head_gap_repaired_without_changing_other_birds(self):
        before = [placement('upper', 896, 608, 160, 98), placement('lower', 757, 707, 242, 335),
                  placement('far', 250, 400, 100, 100)]
        pixels = [p['item']['image'].tobytes() for p in before]
        after = layout._space_contours(before, (84, 320, 1032, 1136), 0)
        self.assert_dimensions(before, after)
        self.assert_gap(after, 24)
        self.assertEqual((after[2]['x'], after[2]['y']), (before[2]['x'], before[2]['y']))
        self.assertTrue(all(math.hypot(a['x']-b['x'], a['y']-b['y']) <= 24 for a, b in zip(after, before)))
        self.assertEqual(pixels, [p['item']['image'].tobytes() for p in before])
        self.assertEqual([(p['x'], p['y']) for p in after],
                         [(p['x'], p['y']) for p in layout._space_contours(before, (84, 320, 1032, 1136), 0)])

    def test_labels_are_part_of_the_gap_and_stay_inside_page(self):
        before = [placement('left', 100, 100, label_width=120, label_height=45),
                  placement('right', 225, 100, label_width=120, label_height=45)]
        after = layout._space_contours(before, (50, 60, 400, 250), 1)
        self.assert_dimensions(before, after)
        self.assert_gap(after, 24, 1)
        self.assertTrue(layout._clear(after, (50, 60, 400, 250), 1))

    def test_page_edge_does_not_clip_collision_padding(self):
        before = [placement('edge', 0, 0, 40, 40), placement('neighbor', 42, 0, 40, 40)]
        after = layout._space_contours(before, (0, 0, 150, 100), 0)
        self.assert_dimensions(before, after)
        self.assert_gap(after, 24)
        self.assertTrue(layout._clear(after, (0, 0, 150, 100), 0))
        # A contour outside the page must fail, even if a page-sized mask
        # would have silently discarded its leftmost native-source pixels.
        clipped = placement('clipped', -1, 0, 40, 40)
        self.assertFalse(layout._clear([clipped], (0, 0, 150, 100), 0))

    def test_thin_opaque_and_antialiased_tips_count(self):
        first = placement('twig', 100, 100, 40, 80)
        image = Image.new('RGBA', (40, 80))
        ImageDraw.Draw(image).ellipse((5, 5, 35, 35), fill=(40, 60, 20, 255))
        ImageDraw.Draw(image).line((20, 30, 20, 79), fill=(40, 60, 20, 1), width=1)
        first['item']['image'] = image
        before = [first, placement('below', 105, 182, 40, 40)]
        after = layout._space_contours(before, (50, 50, 400, 400), 0)
        self.assert_dimensions(before, after)
        self.assert_gap(after, 24)

    def test_already_clear_collection_is_pixel_for_pixel_stable(self):
        before = [placement('left', 100, 100), placement('right', 200, 100)]
        after = layout._space_contours(before, (50, 50, 400, 400), 0)
        self.assertEqual(before, after)

    def test_impossible_gap_keeps_every_bird_at_its_original_size_safely(self):
        before = [placement('left', 0, 0), placement('right', 42, 0)]
        after = layout._space_contours(before, (0, 0, 82, 40), 0)
        self.assert_dimensions(before, after)
        self.assertTrue(layout._clear(after, (0, 0, 82, 40), 0))
        self.assertEqual(before, after)


if __name__ == '__main__':
    unittest.main()
