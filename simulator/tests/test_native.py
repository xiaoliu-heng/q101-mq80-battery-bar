"""Real animation execution and the shipping native stdio transport."""
import json
import select
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from mq80lab.engine import Runtime
from mq80lab.native_rpc import dispatch
from mq80lab.profile import ASSETS,ROOT
from mq80lab.server import Lab

IMAGE=(ASSETS/'battery-bar-v2.bin').read_bytes()


class AnimationTests(unittest.TestCase):
    def test_wave_is_real_animated_code_and_has_main_pixels(self):
        r=Runtime(IMAGE,{'main_off':False,'main_effect':1})
        frames=set()
        for _ in range(20):
            r.preview_advance(50)
            frames.add(bytes(r.u.mem_read(0x81cc4,252)))
        self.assertGreater(len(frames),2)
        self.assertTrue(any(any(x) for x in r.state()['rgb']['main']))
        self.assertIn(0x2001b300,r.covered)
        self.assertIn(0x2000d5b4,r.covered)
        self.assertFalse(r.state()['rgb']['physical_main_mapping_verified'])

    def test_preview_and_discrete_have_same_animation_phase_and_pixels(self):
        a=Runtime(IMAGE,{'main_off':False,'main_effect':1,'side_mode':0})
        b=Runtime(IMAGE,{'main_off':False,'main_effect':1,'side_mode':0})
        a.preview_advance(73);a.render();a.preview_advance(127)
        b.advance(200)
        self.assertEqual(a.state()['rgb'],b.state()['rgb'])
        for address in [0x80ba3,0x80bc7,0x80bc6,0x80b8e,0x80b8f]:
            self.assertEqual(a.byte(address),b.byte(address))

    def test_render_does_not_reset_or_advance_animation_time(self):
        a=Runtime(IMAGE,{'main_off':False})
        b=Runtime(IMAGE,{'main_off':False})
        a.preview_advance(7)
        for _ in range(10):a.render()
        a.preview_advance(3);b.preview_advance(10)
        self.assertEqual(a.state()['rgb'],b.state()['rgb'])
        self.assertEqual(a._last_frame_at,b._last_frame_at)

    def test_speed_uses_actual_firmware_divider(self):
        fast=Runtime(IMAGE,{'main_off':False,'main_speed':0})
        slow=Runtime(IMAGE,{'main_off':False,'main_speed':4})
        # Isolate the actual clock block: a frame may wrap an effect's counter.
        for _ in range(40):
            fast.now_us+=10000;slow.now_us+=10000
            fast._animation_pulse();slow._animation_pulse()
        self.assertEqual(fast.byte(0x80ba3),120)
        self.assertEqual(slow.byte(0x80ba3),24)

    def test_effect_change_during_suspended_call_is_rejected_atomically(self):
        r=Runtime(IMAGE);r.sleep_checkpoint()
        before=r.state();ram=bytes(r.u.mem_read(0x80800,0x200))
        with self.assertRaises(ValueError):r.apply_environment({'main_effect':2,'main_off':False})
        self.assertEqual(before,r.state())
        self.assertEqual(ram,bytes(r.u.mem_read(0x80800,0x200)))

    def test_snapshot_keeps_animation_phase(self):
        a=Runtime(IMAGE,{'main_off':False,'main_effect':3})
        a.preview_advance(123)
        b=Runtime(IMAGE);b.restore(json.loads(json.dumps(a.snapshot())))
        a.preview_advance(347);b.preview_advance(347)
        for field in ['rgb','time_ms','battery','environment','hid']:
            self.assertEqual(a.state()[field],b.state()[field])
        self.assertEqual(bytes(a.u.mem_read(0,0xa0000)),bytes(b.u.mem_read(0,0xa0000)))


class NativeTransportTests(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory()
        self.process=subprocess.Popen([sys.executable,str(ROOT/'run_native.py'),'--data-dir',self.folder.name],
                                      stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        self.identity=0

    def tearDown(self):
        self.process.stdin.close()
        self.process.wait(timeout=10)
        error=self.process.stderr.read().decode()
        self.process.stdout.close();self.process.stderr.close();self.folder.cleanup()
        self.assertEqual(self.process.returncode,0,error)

    def rpc(self,method,params=None,ok=True):
        self.identity+=1
        self.process.stdin.write((json.dumps({'id':self.identity,'method':method,'params':params or {}})+'\n').encode())
        self.process.stdin.flush()
        self.assertTrue(select.select([self.process.stdout],[],[],30)[0],'stdio timeout')
        value=json.loads(self.process.stdout.readline())
        self.assertEqual(value['id'],self.identity)
        self.assertEqual(value['ok'],ok,value)
        return value.get('result') if ok else value['error']

    def test_bootstrap_protocol_contains_native_catalog_not_a_web_url(self):
        result=self.rpc('bootstrap')
        self.assertEqual(result['transport'],'stdio-json-lines')
        self.assertEqual(len(result['layout']),84)
        self.assertEqual(len(result['scenarios']),10)
        self.assertEqual(len(result['firmwares']),6)
        self.assertTrue(any(x['side_hid'] for x in result['firmwares']))
        self.assertTrue(any(x['side_hid_rf'] for x in result['firmwares']))
        self.assertTrue(any(x['firmware_version']=='3.0.0' for x in result['firmwares']))
        self.assertTrue(any(x['firmware_version']=='3.0.1' for x in result['firmwares']))
        self.assertNotIn('url',result)

    def test_query_preview_expiry_and_sleep_first_key_through_native_pipe(self):
        self.assertTrue(self.rpc('action',{'action':'query'})['battery']['query_active'])
        state=self.rpc('action',{'action':'preview','ms':3100})
        self.assertFalse(state['battery']['query_active'])
        self.assertEqual(state['rgb']['side'],[[0,0,0]]*9)
        self.rpc('action',{'action':'sleep'})
        state=self.rpc('action',{'action':'tap','keys':['3,1'],'hold_ms':50})
        self.assertEqual(state['host_text'],'a')
        self.assertEqual(state['hid'],'0000000000000000')
        self.assertTrue(self.rpc('report')['no_device_access'])

    def test_errors_are_actionable_and_do_not_desynchronize_pipe(self):
        before=self.rpc('state')
        self.assertIn('未知',self.rpc('unsupported',ok=False)['message'])
        self.rpc('action',{'action':'environment','values':{'battery_percent':101}},ok=False)
        self.assertEqual(before,self.rpc('state'))
        self.rpc('firmware',{'base64':'not base64'},ok=False)
        self.assertEqual(self.rpc('memory',{'address':'0x80b80','size':8})['bytes'],'0000000000000000')

    def test_snapshot_and_recording_roundtrip_through_native_pipe(self):
        self.rpc('action',{'action':'tap','keys':['3,1'],'hold_ms':50})
        saved=self.rpc('snapshot')
        self.rpc('action',{'action':'tap','keys':['3,2'],'hold_ms':50})
        self.assertEqual(self.rpc('state')['host_text'],'as')
        state=self.rpc('action',{'action':'restore','snapshot':saved})
        self.assertEqual(state['host_text'],'a')
        self.assertEqual(self.rpc('recording')['id'],'after-restore')

    def test_side_hid_color_animation_and_release_through_native_pipe(self):
        image=next(x['id'] for x in self.rpc('bootstrap')['firmwares'] if x['side_hid'])
        self.rpc('action',{'action':'reset','firmware':image,'environment':{'connection':'usb','cable':True}})
        state=self.rpc('action',{'action':'side_hid','mode':'solid','colors':[[255,0,0]]*9})
        self.assertEqual(state['rgb']['side'],[[162,0,0]]*9)
        self.rpc('action',{'action':'side_hid','mode':'rainbow'})
        state=self.rpc('action',{'action':'preview','ms':3100})
        self.assertTrue(state['side_hid']['active'])
        self.assertEqual(state['side_hid']['mode'],'rainbow')
        self.assertGreater(state['side_hid']['frames_sent'],60)
        self.assertFalse(self.rpc('action',{'action':'side_hid','mode':'release'})['side_hid']['active'])
        self.assertEqual(self.rpc('state')['rgb']['side'],[[0,0,0]]*9)
        self.assertTrue(self.rpc('report')['no_device_access'])


if __name__=='__main__':unittest.main(verbosity=2)
