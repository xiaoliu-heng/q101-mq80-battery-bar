"""The native side-light host drives real firmware, including clock and replay."""
import copy
import json
import tempfile
import unittest
from mq80lab.engine import Runtime
from mq80lab.profile import SIDE_HID_IMAGE, SIDE_HID_RF_IMAGE, SIDE_HID_CURRENT_IMAGE
from mq80lab.scenarios import apply_step, expected_bar, run_scenario
from mq80lab.server import Lab

IMAGE = SIDE_HID_IMAGE.read_bytes()
RF_IMAGE = SIDE_HID_CURRENT_IMAGE.read_bytes()


class SideHIDHostTests(unittest.TestCase):
    def runtime(self):
        return Runtime(IMAGE, {'connection': 'usb', 'cable': True, 'battery_percent': 50})

    def test_colors_execute_usb_receivers_and_preserve_main_lights(self):
        r = self.runtime()
        before = r.state()['rgb']['main']
        colors = [[i * 30, 255 - i * 30, 128] for i in range(9)]
        r.side_hid.configure({'mode': 'frame', 'colors': colors, 'brightness': 50})
        self.assertEqual(r.state()['rgb']['side'], [[c * 50 // 100 * 162 // 255 for c in rgb] for rgb in colors])
        self.assertEqual(r.state()['rgb']['main'], before)
        requests = [x['details']['request'] for x in r.trace if x['kind'] == 'raw-hid']
        self.assertEqual([bytes.fromhex(p)[3] & 7 for p in requests], [0, 1])
        self.assertEqual(r.calls['raw_hid_dispatch'], 2)
        self.assertFalse(r.flash.operations)

    def test_rainbow_keeps_streaming_across_long_preview_requests(self):
        r = self.runtime()
        r.side_hid.configure({'mode': 'rainbow'})
        before = r.state()['rgb']['side']
        r.preview_advance(3100)
        self.assertNotEqual(r.state()['rgb']['side'], before)
        self.assertEqual(r.side_hid.state()['frames_sent'], 63)
        self.assertTrue(r.side_hid.state()['active'])
        self.assertFalse(r.flash.operations)

    def test_strict_clock_and_preview_deliver_identical_host_animation(self):
        a, b = self.runtime(), self.runtime()
        for r in [a, b]: r.side_hid.configure({'mode': 'rainbow', 'period_ms': 1000})
        a.advance(250)
        b.preview_advance(250)
        self.assertEqual(a.state()['rgb']['side'], b.state()['rgb']['side'])
        self.assertEqual(a.side_hid.state(), b.side_hid.state())

    def test_battery_query_overrides_stream_then_resumes_without_lease_expiry(self):
        r = self.runtime()
        r.side_hid.configure({'mode': 'rainbow'})
        apply_step(r, {'type': 'query'})
        self.assertEqual(r.state()['rgb']['side'], expected_bar(50))
        self.assertGreater(r.state()['battery']['query_remaining_ms'], 0)
        r.preview_advance(2000)
        self.assertEqual(r.state()['rgb']['side'], expected_bar(50))
        self.assertTrue(r.side_hid.state()['active'])
        r.preview_advance(1100)
        self.assertFalse(r.state()['battery']['query_active'])
        self.assertTrue(r.side_hid.state()['active'])
        self.assertNotEqual(r.state()['rgb']['side'], expected_bar(50))

    def test_release_and_stopping_sender_have_different_recovery_timing(self):
        r = self.runtime()
        r.side_hid.configure({'mode': 'solid'})
        r.preview_advance(2500)
        self.assertTrue(r.side_hid.state()['active'])
        r.side_hid.configure({'mode': 'stop'})
        count = r.side_hid.frames_sent
        r.preview_advance(1999)
        self.assertTrue(r.side_hid.state()['active'])
        r.preview_advance(1)
        self.assertFalse(r.side_hid.state()['active'])
        self.assertEqual(r.side_hid.frames_sent, count)
        self.assertEqual(r.state()['rgb']['side'], [[0, 0, 0]] * 9)
        r.side_hid.configure({'mode': 'rainbow'})
        r.side_hid.configure({'mode': 'release'})
        self.assertFalse(r.side_hid.state()['active'])
        self.assertEqual(r.state()['rgb']['side'], [[0, 0, 0]] * 9)

    def test_snapshot_restores_host_phase_and_old_snapshot_remains_loadable(self):
        a = self.runtime()
        a.side_hid.configure({'mode': 'rainbow', 'period_ms': 1700, 'brightness': 70})
        a.preview_advance(1250)
        saved = json.loads(json.dumps(a.snapshot()))
        b = self.runtime(); b.restore(saved)
        a.preview_advance(370); b.preview_advance(370)
        self.assertEqual(a.state()['rgb'], b.state()['rgb'])
        self.assertEqual(a.side_hid.state(), b.side_hid.state())
        old = copy.deepcopy(saved); old.pop('side_hid_host')
        b.restore(old)
        self.assertEqual(b.side_hid.mode, 'off')
        self.assertEqual(b.state()['rgb']['side'], saved_rgb(saved, self.runtime()))

    def test_reading_state_cannot_renew_lease_or_send_frames(self):
        r = self.runtime(); r.side_hid.configure({'mode': 'rainbow'})
        before = r.snapshot()
        for _ in range(5): r.state(); r.report()
        self.assertEqual(r.snapshot(), before)

    def test_unavailable_usb_stops_transmission_and_reconnect_resumes(self):
        r = self.runtime(); r.side_hid.configure({'mode': 'rainbow'})
        count = r.side_hid.frames_sent
        r.apply_environment({'host_suspended': True})
        r.preview_advance(2100)
        self.assertEqual(r.side_hid.frames_sent, count)
        self.assertFalse(r.side_hid.state()['active'])
        r.apply_environment({'host_suspended': False}); r.preview_advance(50)
        self.assertTrue(r.side_hid.state()['active'])

    def test_invalid_controls_are_atomic_and_stock_cannot_receive_frames(self):
        with tempfile.TemporaryDirectory() as folder:
            lab = Lab(folder)
            before = lab.runtime.snapshot()
            with self.assertRaises(ValueError): lab.action({'action': 'side_hid', 'mode': 'rainbow'})
            self.assertEqual(lab.runtime.snapshot(), before)
            lab.reset(lab.upload(IMAGE, 'side-hid.bin')['id'], {'connection': 'usb', 'cable': True})
            for bad in [{'brightness': 101}, {'period_ms': 0}, {'colors': [[1, 2, True]] * 9}, {'colors': []}, {'unexpected': 1}]:
                before = lab.runtime.snapshot()
                with self.assertRaises(ValueError): lab.action({'action': 'side_hid', 'mode': 'solid', **bad})
                self.assertEqual(lab.runtime.snapshot(), before)

    def test_recorded_color_and_animation_actions_replay_real_output(self):
        with tempfile.TemporaryDirectory() as folder:
            lab = Lab(folder)
            lab.reset(lab.upload(IMAGE, 'side-hid.bin')['id'], {'connection': 'usb', 'cable': True})
            lab.action({'action': 'side_hid', 'mode': 'rainbow', 'period_ms': 1900, 'brightness': 80})
            lab.action({'action': 'preview', 'ms': 1250})
            replay, result = run_scenario(IMAGE, lab.recording)
            self.assertEqual(result['status'], 'PASS', result['error'])
            self.assertEqual(replay.state()['rgb'], lab.runtime.state()['rgb'])
            self.assertEqual(replay.side_hid.state(), lab.runtime.side_hid.state())


def saved_rgb(snapshot, runtime):
    runtime.restore(snapshot)
    return runtime.state()['rgb']['side']


class SideHIDRFTests(unittest.TestCase):
    def runtime(self, **environment):
        return Runtime(RF_IMAGE, {'connection':'rf', 'cable':False, 'battery_percent':50, **environment})

    def test_versioned_image_and_actual_info_reply_agree(self):
        from controller.protocol import packet, decode
        r=self.runtime();p=packet(0,5);state=decode(p,r.receive_rf_hid(p))
        self.assertEqual(state.firmware_version,r.metadata['firmware_version'])
        self.assertEqual(state.firmware_build,r.metadata['firmware_build'])
        self.assertEqual(list(state.firmware_features),r.metadata['firmware_features'])
        self.assertEqual(state.firmware_version,'3.0.1')
        self.assertFalse(r.side_hid.state()['active'])

    def test_unversioned_rf_image_is_preserved_and_supported(self):
        r=Runtime(SIDE_HID_RF_IMAGE.read_bytes(),{'connection':'rf','cable':False})
        self.assertIsNone(r.metadata['firmware_version'])
        r.side_hid.configure({'mode':'rainbow'});r.preview_advance(200)
        self.assertEqual(r.side_hid.frames_sent,3)
        self.assertTrue(r.side_hid.state()['active'])

    def test_rf_color_uses_original_receive_reply_and_closing_acks(self):
        r=self.runtime();before=r.state()['rgb']['main']
        colors=[[i*30,255-i*30,128] for i in range(9)]
        r.side_hid.configure({'mode':'frame','colors':colors,'brightness':50})
        self.assertEqual(r.state()['rgb']['side'],[[v*50//100*162//255 for v in rgb] for rgb in colors])
        self.assertEqual(r.state()['rgb']['main'],before)
        self.assertTrue(r.side_hid.state()['rf_ready'])
        self.assertEqual(r.side_hid.state()['transport'],'virtual-rf')
        self.assertEqual(r.calls['rf_configuration_receive'],2)
        self.assertEqual(r.calls['rf_configuration_closing_ack'],6)
        self.assertEqual(r.byte(0x80b2d),0)
        self.assertNotIn('usb_endpoint_send',r.boundary_counts)
        self.assertFalse(r.flash.operations)

    def test_rf_rainbow_ten_fps_matches_strict_clock_and_battery_priority(self):
        a,b=self.runtime(),self.runtime()
        for r in [a,b]:r.side_hid.configure({'mode':'rainbow'})
        a.advance(250);b.preview_advance(250)
        self.assertEqual(a.state()['rgb'],b.state()['rgb'])
        self.assertEqual(a.side_hid.state(),b.side_hid.state())
        self.assertEqual(a.side_hid.state()['frames_sent'],3)
        apply_step(a,{'type':'query'})
        self.assertEqual(a.state()['rgb']['side'],expected_bar(50))
        a.preview_advance(3100)
        self.assertFalse(a.state()['battery']['query_active'])
        self.assertTrue(a.side_hid.state()['active'])
        self.assertEqual(a.side_hid.state()['fps'],10)

    def test_long_animations_survive_rolling_report_and_packet_buffers(self):
        for connection in ['usb','rf']:
            r=self.runtime(connection=connection,cable=connection=='usb')
            r.side_hid.configure({'mode':'rainbow'})
            for _ in range(12):r.preview_advance(5000)
            expected=1201 if connection=='usb' else 601
            self.assertEqual(r.side_hid.frames_sent,expected)
            self.assertTrue(r.side_hid.state()['active'])
            self.assertFalse(r.flash.operations)

    def test_rf_live_lease_stop_expiry_sleep_and_key_wake(self):
        for finish in ['stop','release']:
            r=self.runtime();r.side_hid.configure({'mode':'rainbow'})
            r.advance(400)
            self.assertEqual(r.word(0x80b30),0)
            with self.assertRaises(ValueError):r.sleep_checkpoint()
            r.side_hid.configure({'mode':finish})
            r.advance(2100)
            self.assertFalse(r.side_hid.state()['active'])
            r.sleep_checkpoint()
            before=r.snapshot()
            with self.assertRaises(ValueError):r.side_hid.configure({'mode':'rainbow'})
            self.assertEqual(before,r.snapshot())
            r.tap(['3,1'])
            self.assertEqual(r.host_text,'a')
            r.side_hid.configure({'mode':'solid'})
            self.assertTrue(r.side_hid.state()['active'])

    def test_rf_disconnect_expires_and_online_receiver_can_resume(self):
        r=self.runtime();r.side_hid.configure({'mode':'rainbow'})
        count=r.side_hid.frames_sent
        r.apply_environment({'receiver':False});r.preview_advance(2100)
        self.assertEqual(r.side_hid.frames_sent,count)
        self.assertFalse(r.side_hid.state()['active'])
        r.apply_environment({'receiver':True});r.preview_advance(100)
        self.assertTrue(r.side_hid.state()['active'])

    def test_rf_snapshot_phase_capabilities_and_recording_replay(self):
        with tempfile.TemporaryDirectory() as folder:
            lab=Lab(folder)
            lab.reset(lab.upload(RF_IMAGE,'rf.bin')['id'],{'connection':'rf','cable':False})
            lab.action({'action':'side_hid','mode':'rainbow','period_ms':1700})
            lab.action({'action':'preview','ms':450})
            replay,result=run_scenario(RF_IMAGE,lab.recording)
            self.assertEqual(result['status'],'PASS',result['error'])
            self.assertEqual(replay.side_hid.state(),lab.runtime.side_hid.state())
            restored=self.runtime();restored.restore(json.loads(json.dumps(lab.runtime.snapshot())))
            for r in [lab.runtime,restored]:r.preview_advance(350)
            self.assertEqual(restored.state()['rgb'],lab.runtime.state()['rgb'])
            self.assertEqual(restored.side_hid.state(),lab.runtime.side_hid.state())

    def test_wireless_typing_with_animation_delay_and_dropped_keyboard_reports(self):
        r=self.runtime(drop_every=3,ack_delay_ms=8)
        r.side_hid.configure({'mode':'rainbow'})
        r.set_keys(['4,0','3,1']);r.advance(300)
        r.side_hid.configure({'mode':'frame','colors':[[0,0,255]]*9})
        r.set_keys([]);r.advance(50)
        self.assertEqual(r.host_text,'A')
        self.assertEqual(r.last_host_hid,bytes(8))
        self.assertGreaterEqual(r.side_hid.frames_sent,5)
        self.assertEqual(r.byte(0x80b2d),0)
        self.assertTrue(r.side_hid.state()['active'])

    def test_legacy_rf_and_ble_are_rejected_before_sending(self):
        for r in [Runtime(IMAGE,{'connection':'rf'}),self.runtime(connection='ble')]:
            before=r.snapshot()
            with self.assertRaises(ValueError):r.side_hid.configure({'mode':'rainbow'})
            self.assertEqual(r.snapshot(),before)


if __name__ == '__main__': unittest.main(verbosity=2)
