"""Original-size collage contracts for the production renderer."""
import unittest
from unittest.mock import patch

import collage
import same_size_collage
from test_collage import CollageTests, PORTRAIT, LANDSCAPE


def geometry(placements):
    return [(p['item']['identity'],p['x'],p['y'],p['w'],p['h'],p['label_width'],p['label_height']) for p in placements]


class OriginalSizeCollageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        CollageTests.setUpClass()
        cls.checker=CollageTests()

    def test_complete_collections_keep_exact_original_dimensions(self):
        for region,safe,count,lines in ((PORTRAIT,(84,320,1032,1136),12,0),
                                       (PORTRAIT,(84,320,1032,1136),None,0),
                                       (LANDSCAPE,(112,240,1376,852),12,0),
                                       (PORTRAIT,(84,320,1032,1136),7,3),
                                       (LANDSCAPE,(112,240,1376,852),7,2)):
            with self.subTest(region=region,count=count,lines=lines):
                items=self.checker.actual_items('log',count)
                original=collage.arrange(items,region,lines)
                rounded=same_size_collage.arrange(items,region,lines,safe_region=safe)
                self.assertEqual({p['item']['identity']:(p['w'],p['h']) for p in original},
                                 {p['item']['identity']:(p['w'],p['h']) for p in rounded})
                self.checker.assert_layout(items,safe,lines,rounded)

    def test_cannot_fit_preserves_original_layout_instead_of_shrinking(self):
        items=self.checker.actual_items('log',7)
        original=collage.arrange(items,PORTRAIT,0)
        self.assertEqual(geometry(original),geometry(same_size_collage.arrange(items,PORTRAIT,0,safe_region=(144,368,2,2))))
        with patch.object(same_size_collage,'_clear',return_value=False):
            result=same_size_collage.arrange(items,PORTRAIT,0,safe_region=(84,320,1032,1136))
        self.assertEqual(geometry(original),geometry(result))

    def test_stable_placements_and_unmodified_source_art(self):
        items=self.checker.actual_items('log',7)
        pixels=[i['image'].tobytes() for i in items]
        first=same_size_collage.arrange(items,PORTRAIT,0,safe_region=(84,320,1032,1136))
        second=same_size_collage.arrange(items,PORTRAIT,0,safe_region=(84,320,1032,1136))
        self.assertEqual(geometry(first),geometry(second))
        self.assertEqual(pixels,[i['image'].tobytes() for i in items])


if __name__=='__main__':unittest.main()
