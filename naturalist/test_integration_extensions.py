"""Integration boundaries for optional art, immutable prints, and previews."""
import copy
from datetime import datetime
import hashlib
import io
import json
import math
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile
from PIL import Image, ImageDraw
import artwork_packs
import bird_details
import classic
import collage
import field_guide
import render
import server
import subject_geometry
import test_collage


class ExtensionIntegrationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.art = self.root / 'bundled'
        self.art.mkdir()
        self.cache = self.root / 'external-cache'
        assets = []
        for index, color in enumerate(((160,80,40,255), (40,80,170,255), (180,160,30,255))):
            image = Image.new('RGBA', (90,110))
            ImageDraw.Draw(image).ellipse((10,4,80,106), fill=color)
            asset = dict(id=f'bird-{index}', scientific_name='Turdus migratorius', common_name='American Robin',
                         creator='Historical artist', title=f'Bird plate {index}', date='1890',
                         source_url=f'https://example.org/plate/{index}', rights='Public domain',
                         rights_url='https://creativecommons.org/publicdomain/mark/1.0/', review={'status':'reviewed'})
            if index < 2:
                path = self.art / f"{asset['id']}.png"
                image.save(path)
                asset.update(file=path.name, sha256=hashlib.sha256(path.read_bytes()).hexdigest())
                assets.append(asset)
            else:
                asset.update(width=image.width, height=image.height, pixel_sha256=hashlib.sha256(image.tobytes()).hexdigest(),
                             rights='CC BY-SA 4.0', rights_url='https://creativecommons.org/licenses/by-sa/4.0/',
                             attribution='Historical artist; prepared by Example editor', changes='Background removed')
                self.pack = dict(id='reviewed', revision='a'*40, assets=[asset])
                path = self.cache / 'reviewed' / ('a'*40) / f"{asset['id']}.png"
                path.parent.mkdir(parents=True)
                image.save(path)
        (self.art/'manifest.json').write_text(json.dumps({'assets':assets}))
        registry = patch.object(artwork_packs, 'registry', return_value=[self.pack])
        registry.start()
        self.addCleanup(registry.stop)
        self.feed = self.new_feed()
        self.feed.settings = render.validate_settings(
            {'schedule':{'enabled':False}, 'dashboard_url':'http://frame.example.org/'}, self.feed.settings, self.feed.assets)
        self.birds = [dict(scientific_name='Turdus migratorius', common_name='American Robin', count=7,
                           latest_heard='12:00:00', max_confidence=.99, hourly_counts=[])]
        blocker = patch.object(classic, '_fetch', side_effect=AssertionError('No network in integration tests'))
        blocker.start()
        self.addCleanup(blocker.stop)

    def new_feed(self):
        return server.Feed('http://birdnet.invalid', self.root/'state', artwork_dir=self.art, pack_directory=self.cache)

    def seed(self, day='2026-10-14', settings=None):
        self.feed.settings = settings or self.feed.settings
        self.feed.bundle = self.feed.build(self.birds, day, self.feed.settings, day+'T12:00:00-06:00')
        server.save_bundle(self.feed.directory, self.feed.bundle)
        self.feed.archive(self.feed.bundle)
        return self.feed.bundle

    def test_external_paths_private_and_license_survives_exports(self):
        self.assertEqual(len(self.feed.assets), 3)
        external = self.feed.assets[-1]
        original = copy.deepcopy(external)
        for style in ('collage','classic'):
            with self.subTest(style=style):
                settings = render.validate_settings(dict(style=style, classic_media='drawings',
                    variants={'Turdus migratorius':'bird-2'}), self.feed.settings, self.feed.assets)
                bundle = self.seed(settings=settings)
                state = self.feed.state()
                encoded = json.dumps(state)
                for private in (str(self.root), '_resolved_path', '_cache_root', '"file"', '"source_file"'):
                    self.assertNotIn(private, encoded)
                self.assertEqual(state['artwork_credits'][0]['id'], 'bird-2')
                self.assertEqual(state['print_rights']['rights'], 'CC BY-SA 4.0')
                self.assertIn('dithered', state['print_rights']['changes'])
                for key in ('art','preview'):
                    with Image.open(io.BytesIO(bundle[key])) as image:
                        self.assertEqual(json.loads(image.info['Print rights']), state['print_rights'])
                        credit, = json.loads(image.info['Artwork sources'])
                        self.assertEqual(credit['source_url'], external['source_url'])
                        self.assertEqual(credit['attribution'], external['attribution'])
                saved = json.loads(self.feed.history.archived('2026-10-14','state.json'))
                self.assertEqual(saved['artwork_credits'], state['artwork_credits'])
                with zipfile.ZipFile(io.BytesIO(self.feed.history.archived('2026-10-14','download.zip'))) as archive:
                    self.assertEqual(archive.read('current_large.bin'), bundle['frame'])
                    self.assertEqual(json.loads(archive.read('state.json'))['print_rights'], state['print_rights'])
        self.assertEqual(external, original)

    def test_license_changes_only_metadata(self):
        image = Image.new('RGB',(1200,1600),(240,230,215))
        before = render.pack(image)
        render.add_print_rights(image,[self.feed.assets[-1]])
        after = render.pack(image)
        self.assertEqual(before[0], after[0])
        with Image.open(io.BytesIO(before[1])) as left, Image.open(io.BytesIO(after[1])) as right:
            self.assertEqual(left.tobytes(), right.tobytes())
            self.assertEqual(json.loads(right.info['Print rights'])['rights'], 'CC BY-SA 4.0')
        render.add_print_rights(image,[self.feed.assets[0]])
        self.assertNotIn('Print rights',image.info)

    def test_archived_bird_details_keep_original_photo_credits(self):
        settings = render.validate_settings({'style':'classic'},self.feed.settings,self.feed.assets)
        def photo(identity):
            return Image.new('RGB',(50,60),'blue'), dict(id=identity,creator=identity,title='Robin photograph',
                rights='CC BY 4.0',rights_url='https://creativecommons.org/licenses/by/4.0/',
                source_url=f'https://example.org/{identity}',attribution=identity)
        with patch.object(classic,'photo',return_value=photo('old-photographer')):
            old = self.seed('2026-10-13',settings)
        with patch.object(classic,'photo',return_value=photo('new-photographer')):
            self.seed('2026-10-14',settings)
        self.feed.assets = []
        def fetch(base,path):
            return [] if '/summary?' in path else {'data':[],'total':0}
        with patch.object(bird_details,'fetch',side_effect=fetch):
            result = self.feed.details('Turdus migratorius','2026-10-13')
        self.assertEqual(result['artworks'],old['state']['artwork_credits'])
        self.assertEqual(result['artworks'][0]['creator'],'old-photographer')
        self.assertEqual(self.feed.history.archived('2026-10-13','art.png'),old['art'])

    def test_daily_choice_stable_and_overnight_edits_wait_across_restart(self):
        original = self.seed()
        chosen = original['state']['stats']['species'][0]['artwork_id']
        self.feed.update_settings({'show_names':True})
        self.assertEqual(self.feed.bundle['state']['stats']['species'][0]['artwork_id'],chosen)
        self.feed = self.new_feed()
        self.assertEqual(self.feed.bundle['state']['stats']['species'][0]['artwork_id'],chosen)
        last = copy.deepcopy(self.feed.bundle)
        with patch.object(server,'schedule_state',return_value=dict(active=False,sleep_seconds=3600)):
            self.feed.update_settings({'variants':{'Turdus migratorius':'bird-2'},'show_qr':False})
            self.feed = self.new_feed()
            self.assertTrue(self.feed.state()['pending_settings'])
            self.assertEqual(self.feed.bundle['frame'],last['frame'])
            self.assertTrue(self.feed.bundle['state']['display_settings']['show_qr'])
            with patch.object(server,'get_json',side_effect=AssertionError('Sleeping frame must not fetch')):
                self.feed.refresh()
            self.assertEqual([r['date'] for r in self.feed.history.days()],['2026-10-14'])
        class Tomorrow(datetime):
            @classmethod
            def now(cls,tz=None):
                return cls(2026,10,15,12,tzinfo=tz)
        with patch.object(server,'datetime',Tomorrow),patch.object(server,'get_json',return_value=self.birds):
            self.feed.refresh()
        self.assertEqual(self.feed.bundle['state']['stats']['date'],'2026-10-15')
        self.assertEqual(self.feed.bundle['state']['stats']['species'][0]['artwork_id'],'bird-2')
        self.assertFalse(self.feed.state()['pending_settings'])
        self.assertEqual([r['date'] for r in self.feed.history.days()],['2026-10-15','2026-10-14'])

    def test_color_previews_immutable_and_restore_displayed_qr(self):
        settings = render.validate_settings({'variants':{'Turdus migratorius':'bird-2'}},self.feed.settings,self.feed.assets)
        self.seed(settings=settings)
        with patch.object(server,'schedule_state',return_value=dict(active=False,sleep_seconds=3600)):
            self.feed.update_settings({'show_qr':False,'dashboard_url':'https://different.example.org/'})
        before = copy.deepcopy(self.feed.bundle)
        saved = (self.feed.directory/'current.zip').read_bytes()
        original = Image.open(io.BytesIO(before['art'])).convert('RGB')
        render.add_qr(original,before['state']['display_settings'])
        bounds = original.info['QR bounds']
        expected = Image.open(io.BytesIO(before['preview'])).crop(bounds).tobytes()
        revision = before['state']['revision']
        self.assertEqual(self.feed.color_preview('current',revision),before['preview'])
        for profile in ('compensated_floyd','compensated_stucki'):
            with self.subTest(profile=profile):
                output = self.feed.color_preview(profile,revision)
                with Image.open(io.BytesIO(output)) as image:
                    self.assertEqual(image.crop(bounds).tobytes(),expected)
                    self.assertEqual(json.loads(image.info['Print rights']),before['state']['print_rights'])
                with patch.object(render,'pack',side_effect=AssertionError('Expected cached preview')):
                    self.assertEqual(self.feed.color_preview(profile,revision),output)
        with self.assertRaises(RuntimeError):
            self.feed.color_preview('compensated_floyd','stale-revision')
        self.assertEqual(self.feed.bundle,before)
        self.assertEqual((self.feed.directory/'current.zip').read_bytes(),saved)


class ActualBirdGeometryTests(unittest.TestCase):
    def test_fourteen_observed_birds_fit_with_reviewed_subject_sizing(self):
        # October 14 species/count snapshot; no station or recording identifiers.
        observations = [('Sturnus vulgaris',41),('Haemorhous mexicanus',37),('Bombycilla cedrorum',23),
            ('Poecile atricapillus',8),('Spinus psaltria',7),('Colaptes auratus',6),('Sitta canadensis',6),
            ('Turdus migratorius',4),('Coccothraustes vespertinus',2),('Columba livia',2),
            ('Charadrius vociferus',1),('Corvus brachyrhynchos',1),('Pica hudsonia',1),('Spinus tristis',1)]
        root = Path(render.__file__).parent/'artwork'
        with tempfile.TemporaryDirectory() as no_packs:
            assets = render.catalogue(root,pack_directory=no_packs)
        names = {a['scientific_name']:a['common_name'] for a in assets}
        birds = [dict(scientific_name=name,common_name=names[name],count=count) for name,count in observations]
        checker = test_collage.CollageTests()
        for style,orientation,labels,scale in (('collage','portrait',False,'log'),('collage','portrait',True,'equal'),
                                               ('collage','landscape',True,'linear'),('field_guide','portrait',True,'log')):
            with self.subTest(style=style,orientation=orientation,scale=scale):
                settings = render.validate_settings(dict(style=style,orientation=orientation,scale=scale,
                    show_names=labels,show_counts=labels,show_scientific=labels),{},assets)
                module = field_guide if style=='field_guide' else collage
                arrange = module.arrange
                def verify(items,region,lines):
                    for item in items:
                        item['identity'] = item['bird']['scientific_name']
                        with Image.open(artwork_packs.asset_path(item['asset'],root)) as source:
                            area = subject_geometry.subject_area(item['asset'],source.convert('RGBA'))
                        expected = math.sqrt(render.area_weight(item['bird']['count'],scale)/area)
                        self.assertAlmostEqual(item['w']/item['image'].width,expected)
                    placements = arrange(items,region,lines)
                    checker.assert_layout(items,region,lines,placements)
                    return placements
                with patch.object(module,'arrange',side_effect=verify):
                    canvas,selected = render.compose(birds,settings,assets,root,date='2026-10-14')
                self.assertEqual(len(selected),14)
                hotspots = canvas.info['Bird hotspots']
                self.assertEqual({h['scientific_name'] for h in hotspots},{name for name,_ in observations})
                for hotspot in hotspots:
                    mask = hotspot['alpha_mask']
                    self.assertEqual(len(mask['rows']),mask['height'])
                    self.assertTrue(all(len(row)==mask['width'] and set(row)<={'0','1'} for row in mask['rows']))
                    self.assertIn('1',''.join(mask['rows']))


if __name__=='__main__':
    unittest.main()
