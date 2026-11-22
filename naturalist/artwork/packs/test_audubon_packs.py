"""Only reviewed, pinned contours may turn original plates into optional art."""
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch, Mock

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import artwork_packs as packs


class AudubonPackTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.metadata = self.root/'metadata'
        self.metadata.mkdir()
        self.registry_path = self.metadata/'audubon-public-domain-reviewed.json'
        self.sources = self.root/'local-sources'
        self.sources.mkdir()
        source = Image.new('RGB',(32,24))
        source.putdata([(x*7,y*9,(x+y)*4) for y in range(24) for x in range(32)])
        source.save(self.sources/'plate.jpg',quality=95)
        self.source_bytes = (self.sources/'plate.jpg').read_bytes()
        source_hash = hashlib.sha256(self.source_bytes).hexdigest()
        self.recipes = {'schema_version':1,'assets':[{'id':f'cutout-{i}','source_id':'plate','crop':[i,0,16+i,20]}
                                                    for i in range(2)]}
        self.pack = dict(id='audubon-public-domain-reviewed',kind=packs.AUDUBON_KIND,title='Reviewed Audubon',
            source_url='https://commons.wikimedia.org/wiki/File:Plate.jpg',rights='Public domain',
            rights_url='https://creativecommons.org/publicdomain/mark/1.0/',
            attribution='John James Audubon; contour preparation by FederFunk',
            attribution_url='https://commons.wikimedia.org/wiki/File:Plate.jpg',recipe_file='contours.json',
            sources={'plate':dict(filename='plate.jpg',url='https://upload.wikimedia.org/wikipedia/commons/a/ab/Plate.jpg',
                                  sha256=source_hash,width=32,height=24)},assets=[])
        for recipe in self.recipes['assets']:
            image = self.build(self.sources/'plate.jpg',recipe)
            pixels = hashlib.sha256(image.tobytes()).hexdigest()
            self.pack['assets'].append(dict(id=recipe['id'],recipe_id=recipe['id'],source_id='plate',
                scientific_name='Spinus pinus',common_name='Pine Siskin',width=image.width,height=image.height,
                source_sha256=source_hash,pixel_sha256=pixels,rights='Public domain',rights_url=self.pack['rights_url'],
                attribution=self.pack['attribution'],source_url=self.pack['source_url'],changes='Original RGB pixels; contour alpha',
                review={'status':'reviewed','reviewed_source_sha256':source_hash,'reviewed_pixel_sha256':pixels}))
        self.save_metadata()
        self.cache = self.root/'external-cache'
        for context in (patch.object(packs,'registry',return_value=[]),patch.object(packs,'AUDUBON_REGISTRY',self.registry_path),
                        patch.object(packs,'HABIRD_REGISTRY',self.metadata/'no-flight-pack.json'),
                        patch.object(packs,'BATS_REGISTRY',self.metadata/'no-bat-pack.json'),
                        patch.dict(sys.modules,{'contour_artwork':types.SimpleNamespace(build_cutout=self.build)})):
            context.start()
            self.addCleanup(context.stop)

    @staticmethod
    def build(path,recipe):
        # Fixture builder exercises the boundary; production contour geometry
        # has its own tests and is never accepted without its reviewed digest.
        with Image.open(path) as image:
            return image.crop(recipe['crop']).convert('RGBA')

    def save_metadata(self):
        encoded = json.dumps(self.recipes).encode()
        (self.metadata/'contours.json').write_bytes(encoded)
        self.pack['recipe_sha256'] = self.pack['revision'] = hashlib.sha256(encoded).hexdigest()
        self.registry_path.write_text(json.dumps(self.pack))

    def test_local_verified_originals_keep_pixels_credits_and_public_permissions(self):
        prior_umask = os.umask(0o077)
        try:
            with patch.object(packs.urllib.request.OpenerDirector,'open',side_effect=AssertionError('No download')):
                self.assertEqual(packs.install_pack(self.pack['id'],self.cache,self.sources),2)
        finally:
            os.umask(prior_umask)
        assets = packs.load_pack_assets(self.cache)
        self.assertEqual(len(assets),2)
        for asset,recipe in zip(assets,self.recipes['assets']):
            path = packs.asset_path(asset,self.root)
            with Image.open(path) as image:
                self.assertEqual(image.convert('RGBA').tobytes(),self.build(self.sources/'plate.jpg',recipe).tobytes())
                self.assertEqual(image.info['rights'],'Public domain')
            self.assertEqual(path.stat().st_mode&0o777,0o644)
            for directory in (self.cache,self.cache/self.pack['id'],path.parent):
                self.assertEqual(directory.stat().st_mode&0o777,0o755)
            self.assertNotIn(str(self.root),json.dumps(packs.public_asset(asset)))
        status, = packs.packs_status(self.cache)
        self.assertEqual(status['installed_count'],2)
        self.assertEqual(status['attribution'],self.pack['attribution'])
        self.assertNotIn('Fugleramme',status['attribution'])
        self.assertEqual((path.parent/'sources/plate.jpg').read_bytes(),self.source_bytes)

    def test_explicit_network_install_fetches_original_once_for_shared_plate(self):
        opener = Mock()
        opener.open.side_effect = lambda *args,**kwargs: io.BytesIO(self.source_bytes)
        with patch.object(packs.urllib.request,'build_opener',return_value=opener):
            self.assertEqual(packs.install_pack(self.pack['id'],self.cache),2)
            self.assertEqual(opener.open.call_count,1)
            self.assertEqual(opener.open.call_args.args[0].full_url,self.pack['sources']['plate']['url'])
            packs.install_pack(self.pack['id'],self.cache)
            self.assertEqual(opener.open.call_count,1)  # Hash-verified external source cache.

    def test_modified_recipe_source_or_builder_output_never_replaces_prepared_file(self):
        packs.install_pack(self.pack['id'],self.cache,self.sources)
        asset = packs.load_pack_assets(self.cache)[0]
        path = packs.asset_path(asset,self.root)
        previous = path.read_bytes()
        (self.metadata/'contours.json').write_text('{}')
        with self.assertRaisesRegex(ValueError,'recipe hash'):
            packs.install_pack(self.pack['id'],self.cache,self.sources)
        self.save_metadata()
        (self.sources/'plate.jpg').write_bytes(b'changed source')
        with self.assertRaisesRegex(ValueError,'Source artwork hash'):
            packs.install_pack(self.pack['id'],self.cache,self.sources)
        (self.sources/'plate.jpg').write_bytes(self.source_bytes)
        with patch.dict(sys.modules,{'contour_artwork':types.SimpleNamespace(build_cutout=lambda *a: Image.new('RGBA',(16,20),'red'))}):
            with self.assertRaisesRegex(ValueError,'reviewed pixels'):
                packs.install_pack(self.pack['id'],self.cache,self.sources)
        self.assertEqual(path.read_bytes(),previous)

    def test_unreviewed_wrong_dimensions_and_size_limits_fail_closed(self):
        original = copy.deepcopy(self.pack)
        for field,value in (('status','pending'),('reviewed_pixel_sha256','0'*64),('reviewed_source_sha256','0'*64)):
            self.pack=copy.deepcopy(original)
            self.pack['assets'][0]['review'][field]=value
            self.save_metadata()
            with self.assertRaisesRegex(ValueError,'recorded source and pixel review'):
                packs.install_pack(self.pack['id'],self.cache,self.sources)
        self.pack=copy.deepcopy(original)
        self.pack['sources']['plate']['width']=33
        self.save_metadata()
        with self.assertRaisesRegex(ValueError,'dimensions'):
            packs.install_pack(self.pack['id'],self.cache,self.sources)
        self.pack=copy.deepcopy(original)
        self.save_metadata()
        with patch.object(packs,'MAX_SOURCE_BYTES',len(self.source_bytes)-1):
            with self.assertRaisesRegex(ValueError,'download limit'):
                packs.install_pack(self.pack['id'],self.cache,self.sources)
        with patch.object(packs,'MAX_SOURCE_PIXELS',10):
            with self.assertRaisesRegex(ValueError,'pixel limit'):
                packs.install_pack(self.pack['id'],self.cache,self.sources)
        with patch.object(packs,'MAX_BYTES',1):
            with self.assertRaisesRegex(ValueError,'size limit'):
                packs.install_pack(self.pack['id'],self.cache,self.sources)
        self.assertEqual(packs.load_pack_assets(self.cache),[])

    def test_urls_paths_and_symlinks_cannot_escape_registered_sources(self):
        original = copy.deepcopy(self.pack)
        for url in ('https://example.org/plate.jpg','http://upload.wikimedia.org/wikipedia/commons/a/Plate.jpg',
                    'https://upload.wikimedia.org/wikipedia/commons/thumb/a/Plate.jpg','https://upload.wikimedia.org/wikipedia/commons/a/Plate.jpg?x=1'):
            self.pack=copy.deepcopy(original)
            self.pack['sources']['plate']['url']=url
            self.save_metadata()
            with self.assertRaisesRegex(ValueError,'original Commons'):
                packs.install_pack(self.pack['id'],self.cache,self.sources)
        self.pack=copy.deepcopy(original)
        self.pack['recipe_file']='../outside.json'
        self.save_metadata()
        with self.assertRaisesRegex(ValueError,'Unsafe'):
            packs.install_pack(self.pack['id'],self.cache,self.sources)
        self.pack=copy.deepcopy(original)
        self.save_metadata()
        source=self.sources/'plate.jpg'
        source.unlink()
        outside=self.root/'outside.jpg'
        outside.write_bytes(self.source_bytes)
        source.symlink_to(outside)
        with self.assertRaisesRegex(ValueError,'escapes'):
            packs.install_pack(self.pack['id'],self.cache,self.sources)
        escape_cache=self.root/'escape-cache'
        escape_cache.mkdir()
        (escape_cache/self.pack['id']).symlink_to(self.sources,target_is_directory=True)
        with self.assertRaisesRegex(ValueError,'escapes'):
            packs.install_pack(self.pack['id'],escape_cache,self.sources)

    def test_missing_pack_has_no_download_or_cache_side_effects(self):
        with patch.object(packs.urllib.request.OpenerDirector,'open',side_effect=AssertionError('No download')):
            self.assertEqual(packs.load_pack_assets(self.cache),[])
            self.assertEqual(packs.packs_status(self.cache)[0]['status'],'not-installed')
        self.assertFalse(self.cache.exists())
        self.registry_path.unlink()
        self.assertEqual(packs.registered_packs(),[])

    def test_jpeg_header_bound_does_not_change_pillow_guard(self):
        guard=Image.MAX_IMAGE_PIXELS
        path=self.root/'oversized.jpg'
        # Claimed 20001 x 10000 source, without allocating or decoding pixels.
        path.write_bytes(b'\xff\xd8\xff\xc0\x00\x11\x08'+(10000).to_bytes(2,'big')+(20001).to_bytes(2,'big')+b'\x03'*10)
        with self.assertRaisesRegex(ValueError,'pixel limit'):
            packs._jpeg_dimensions(path)
        self.assertEqual(Image.MAX_IMAGE_PIXELS,guard)
        path.write_bytes(b'not a jpeg')
        with self.assertRaisesRegex(ValueError,'JPEG source'):
            packs._jpeg_dimensions(path)


if __name__=='__main__':
    unittest.main()
