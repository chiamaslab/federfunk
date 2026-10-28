"""Contour extraction preserves source pigment and measurable visible toes."""
import copy
import hashlib
from pathlib import Path
import tempfile
import unittest

from PIL import Image, ImageDraw

import contour_artwork
import subject_geometry


class ContourArtworkTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name)/'source.jpg'
        image = Image.new('RGB',(100,80))
        image.putdata([(x*2,y*3,(x+y)%256) for y in range(80) for x in range(100)])
        image.save(self.path,quality=96)
        with Image.open(self.path) as source:
            self.original=source.copy()
        # One body, thin visible toes, and a retained perch. The gap below the
        # body is paper and must remain transparent, including between toes.
        body=[[20,10],[60,10],[60,35],[20,35]]
        toes=[[30,35],[32,35],[32,48],[39,48],[39,50],[30,50]]
        perch=[[15,51],[65,51],[65,57],[15,57]]
        self.recipe=dict(reference_width=100,polygons=[body,toes,perch],exclude=[],
                         subject_polygons=[body,toes],subject_exclude=[],max_size=1400,
                         alpha_edge_blur=0,rotation_degrees=0)

    def test_native_crop_keeps_original_rgb_and_gaps(self):
        before=self.path.read_bytes()
        result,geometry=contour_artwork.build_cutout_with_geometry(self.path,self.recipe)
        # Opaque contour bounds are x15..65 inclusive, y10..57 inclusive.
        self.assertEqual(result.size,(51,48))
        self.assertEqual(result.convert('RGB').tobytes(),self.original.crop((15,10,66,58)).tobytes())
        self.assertEqual(result.getpixel((31-15,42-10))[3],255)  # Real toe.
        self.assertEqual(result.getpixel((45-15,42-10))[3],0)    # Open paper gap.
        self.assertEqual(result.getpixel((50-15,53-10))[3],255)  # Retained perch.
        self.assertEqual(self.path.read_bytes(),before)
        again=contour_artwork.build_cutout(self.path,self.recipe)
        self.assertEqual(again.tobytes(),result.tobytes())
        self.assertEqual(geometry['size'],list(result.size))

    def test_exclusion_mask_changes_only_alpha(self):
        recipe=copy.deepcopy(self.recipe)
        recipe['exclude']=[[[25,15],[30,15],[30,20],[25,20]]]
        result=contour_artwork.build_cutout(self.path,recipe)
        self.assertEqual(result.getpixel((27-15,17-10))[3],0)
        self.assertEqual(result.convert('RGB').tobytes(),self.original.crop((15,10,66,58)).tobytes())
        self.assertEqual(result.getpixel((35-15,17-10))[3],255)

    def test_normalized_subject_area_includes_toes_and_excludes_retained_perch(self):
        result,geometry=contour_artwork.build_cutout_with_geometry(self.path,self.recipe)
        for polygons in (geometry['polygons'],geometry['exclude']):
            for polygon in polygons:
                self.assertTrue(all(0<=v<=1 for point in polygon for v in point))
        digest=hashlib.sha256(result.tobytes()).hexdigest()
        geometry.update(pixel_sha256=digest,review={'status':'reviewed'})
        asset=dict(id='fixture',pixel_sha256=digest,subject_geometry=geometry)
        mask=subject_geometry.subject_mask(asset,result)
        self.assertEqual(mask.getpixel((31-15,42-10)),255)
        self.assertEqual(mask.getpixel((50-15,53-10)),0)
        expected=Image.new('L',self.original.size)
        draw=ImageDraw.Draw(expected)
        for polygon in self.recipe['subject_polygons']:
            draw.polygon([tuple(p) for p in polygon],fill=255)
        self.assertEqual(subject_geometry.subject_area(asset,result),sum(expected.histogram()[1:]))
        self.assertTrue(subject_geometry.geometry_status(asset,result)['reviewed'])

    def test_reference_scale_and_final_size_are_deterministic(self):
        half=copy.deepcopy(self.recipe)
        half['reference_width']=50
        for key in ('polygons','exclude','subject_polygons','subject_exclude'):
            half[key]=[[[x/2,y/2] for x,y in polygon] for polygon in half[key]]
        self.assertEqual(contour_artwork.build_cutout(self.path,half).tobytes(),
                         contour_artwork.build_cutout(self.path,self.recipe).tobytes())
        half['max_size']=25
        result,geometry=contour_artwork.build_cutout_with_geometry(self.path,half)
        self.assertEqual(max(result.size),25)
        self.assertEqual(geometry['size'],list(result.size))
        self.assertEqual(result.tobytes(),contour_artwork.build_cutout(self.path,half).tobytes())

    def test_invalid_source_coordinates_limits_and_empty_contours_rejected(self):
        invalid=[]
        for point in ((-1,10),(101,10),(10,81),(float('nan'),10),(float('inf'),10)):
            item=copy.deepcopy(self.recipe)
            item['polygons'][0][0]=point
            invalid.append(item)
        for key,value in (('reference_width',0),('max_size',1401),('alpha_edge_blur',2),('rotation_degrees',90)):
            invalid.append({**self.recipe,key:value})
        invalid.append({**self.recipe,'polygons':[]})
        invalid.append({**self.recipe,'exclude':[[[0,0],[99,0],[99,79],[0,79]]]})
        invalid.append({**self.recipe,'polygons':[[[25,10],[25,20],[25,30]]], 'subject_polygons':[]})
        for index,recipe in enumerate(invalid):
            with self.subTest(case=index),self.assertRaises(ValueError):
                contour_artwork.build_cutout(self.path,recipe)


if __name__=='__main__':
    unittest.main()
