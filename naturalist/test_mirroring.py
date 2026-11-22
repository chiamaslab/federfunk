"""Daily reflections preserve source pixels, geometry and saved-print meaning."""
from copy import deepcopy
from datetime import date
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image, ImageDraw
import classic
import render
import server
import meaningful_updates


class MirroringTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.picture = Image.new('RGBA', (180, 120))
        draw = ImageDraw.Draw(self.picture)
        draw.rectangle((12, 15, 55, 50), fill=(170, 70, 40, 255))
        draw.rectangle((10, 70, 150, 100), fill=(70, 90, 30, 255))
        self.path = self.root/'bird.png'
        self.picture.save(self.path)
        self.asset = dict(id='asymmetric-bird', scientific_name='Example bird', common_name='Example',
                          file='bird.png', sha256=hashlib.sha256(self.path.read_bytes()).hexdigest(),
                          title='Example illustration', source_url='https://example.org/plate',
                          rights='Public domain', review={'status':'reviewed'}, changes='Original source')
        self.asset['subject_geometry'] = dict(sha256=self.asset['sha256'], size=[180,120],
            polygons=[[[12/179,15/119],[55/179,15/119],[55/179,50/119],[12/179,50/119]]],
            review={'status':'reviewed'})
        self.birds = [dict(scientific_name='Example bird', common_name='Example', count=5, latest_heard='10:00:00')]
        self.settings = render.validate_settings({'show_qr':True, 'dashboard_url':'http://frame.example.org/',
            'show_names':True, 'schedule':{'enabled':False}}, {}, [self.asset])

    def test_daily_choice_is_stable_independent_and_overrides_win(self):
        config = {**self.settings, 'mirror_mode':'daily'}
        before = deepcopy(self.asset)
        choices = []
        other = []
        for day in range(1,29):
            stamp = date(2026,10,day)
            a = render.artwork_for_display(self.asset,config,stamp)
            b = render.artwork_for_display(self.asset,config,stamp.isoformat())
            self.assertEqual(a,b)
            choices.append(a['display_mirrored'])
            other.append(render.artwork_for_display({**self.asset,'id':'other-bird'},config,stamp)['display_mirrored'])
        self.assertEqual(set(choices),{False,True})
        self.assertNotEqual(choices,other)
        for mode, expected in (('original',False),('mirrored',True)):
            config['mirror_overrides'] = {'Example bird':mode}
            self.assertEqual(render.artwork_for_display(self.asset,config,'2026-10-29')['display_mirrored'],expected)
        self.assertEqual(self.asset,before)
        self.assertEqual(hashlib.sha256(self.path.read_bytes()).hexdigest(),self.asset['sha256'])

    def test_settings_reject_unknown_birds_modes_and_unbounded_overrides(self):
        for values in ({'mirror_mode':'random-every-poll'}, {'mirror_overrides':[]},
                       {'mirror_overrides':{'Absent bird':'mirrored'}},
                       {'mirror_overrides':{'Example bird':True}}):
            with self.assertRaises(ValueError):
                render.validate_settings(values,self.settings,[self.asset])
        self.assertEqual(render.validate_settings({}, {}, [self.asset])['mirror_mode'],'original')

    def compose(self, mode, style='collage'):
        captured = []
        def arrange(items,*args,**kwargs):
            captured.extend(items)
            return [dict(item=item,w=item['image'].width,h=item['image'].height,x=300,y=500,cx=370,
                         label_width=300,label_height=39) for item in items]
        module = 'same_size_collage' if style=='collage' else 'field_guide'
        settings = {**self.settings,'style':style,'mirror_overrides':{'Example bird':mode}}
        with patch(module+'.arrange',side_effect=arrange):
            canvas,selected = render.compose(self.birds,settings,[self.asset],self.root,date='2026-10-29')
        return canvas, selected, captured[0]

    def test_pixels_body_bounds_click_mask_labels_and_qr_follow_correct_transform(self):
        for style in ('collage','field_guide'):
            original,_,a = self.compose('original',style)
            mirrored,selected,b = self.compose('mirrored',style)
            expected = a['image'].transpose(Image.Transpose.FLIP_LEFT_RIGHT)
            self.assertEqual(expected.tobytes(),b['image'].tobytes())
            self.assertEqual(a['w'],b['w'])
            self.assertEqual(a['h'],b['h'])
            left,top,right,bottom = a['body_bounds']
            self.assertEqual(b['body_bounds'],(a['image'].width-right,top,a['image'].width-left,bottom))
            h0,h1 = original.info['Bird hotspots'][0],mirrored.info['Bird hotspots'][0]
            self.assertEqual(h1['alpha_mask']['rows'],[row[::-1] for row in h0['alpha_mask']['rows']])
            self.assertTrue(h1['artwork_mirrored'])
            self.assertEqual(original.crop((100,610,650,700)).tobytes(),mirrored.crop((100,610,650,700)).tobytes())
            qr = original.info['QR bounds']
            self.assertEqual(original.crop(qr).tobytes(),mirrored.crop(qr).tobytes())
            self.assertTrue(selected[0][1]['display_mirrored'])
            self.assertIn('horizontally mirrored',selected[0][1]['changes'])
            self.assertNotIn('display_mirrored',self.asset)
            self.assertTrue(json.loads(mirrored.info['Artwork sources'])[0]['display_mirrored'])

    def test_png_exports_preserve_complete_source_license_notice(self):
        notice = "Copyright (c) Example artist\nPermission and warranty terms."
        self.asset.update(rights="MIT", license_notice=notice)
        for style in ('collage', 'field_guide'):
            with self.subTest(style=style):
                canvas, _, _ = self.compose('mirrored', style)
                _, preview = render.pack(canvas)
                for exported in (render.png(canvas), preview):
                    with Image.open(io.BytesIO(exported)) as reopened:
                        credits = json.loads(reopened.info['Artwork sources'])
                    self.assertEqual(credits[0]['license_notice'], notice)
                    self.assertTrue(credits[0]['display_mirrored'])

    def feed(self):
        feed = server.Feed('http://unused.invalid',self.root/'state',assets=[self.asset],artwork_dir=self.root)
        feed.settings = self.settings
        return feed

    def test_classic_illustration_uses_exact_reflection_and_records_credits(self):
        feed = self.feed()
        with patch.object(classic,'compose',wraps=classic.compose) as compose:
            _,selected,_,credits = feed._compose(self.birds,'2026-10-29',
                {**self.settings,'style':'classic','classic_media':'drawings',
                 'mirror_overrides':{'Example bird':'mirrored'}})
        self.assertEqual(compose.call_args.args[2].tobytes(),
                         self.picture.transpose(Image.Transpose.FLIP_LEFT_RIGHT).tobytes())
        self.assertTrue(credits[0]['display_mirrored'])
        self.assertTrue(selected[0][1]['display_mirrored'])

    def test_direction_change_is_meaningful_and_night_edits_preserve_saved_art(self):
        feed = self.feed()
        old = feed.build(self.birds,'2026-10-29',self.settings,'2026-10-29T12:00:00-06:00')
        new = feed.build(self.birds,'2026-10-29',
            {**self.settings,'mirror_overrides':{'Example bird':'mirrored'}},'2026-10-29T12:01:00-06:00')
        old_state = deepcopy(old['state'])
        new_state = deepcopy(new['state'])
        new_state['settings'] = new_state['display_settings'] = old_state['settings']
        config = {**self.settings,'update_mode':'meaningful'}
        self.assertEqual(meaningful_updates.publish_reason(old_state,new_state,config),'artwork')
        feed.bundle = old
        server.save_bundle(feed.directory,old)
        feed.archive(old)
        archive = feed.history.archived('2026-10-29','download.zip')
        with patch.object(server,'schedule_state',return_value=dict(active=False,sleep_seconds=3600)):
            feed.update_settings({'mirror_mode':'daily'})
            self.assertTrue(feed.state()['pending_settings'])
        self.assertEqual(feed.bundle['frame'],old['frame'])
        self.assertEqual(feed.bundle['art'],old['art'])
        self.assertEqual(feed.bundle['state']['display_settings']['mirror_mode'],'original')
        self.assertEqual(feed.history.archived('2026-10-29','download.zip'),archive)


if __name__=='__main__':
    unittest.main()
