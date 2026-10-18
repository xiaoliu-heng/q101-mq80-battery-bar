"""Execute local notification firmware offline with modeled hardware only."""
import argparse
import hashlib
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT/'simulator')]
from controller.protocol import packet, decode, effect_payload, Client, UnsupportedFirmware
from controller.transports import SimulatedTransport
from mq80lab.engine import Runtime
from mq80lab.scenarios import apply_step, expected_bar

IMAGE = b''
MANIFEST = {}
HOST, SIDE = 0x8269c, 0x81dc0
PATHS = ['direct', 'general', 'rf']


def runtime(path='direct'):
    return Runtime(IMAGE, {'connection': 'rf' if path=='rf' else 'usb',
                           'cable': path!='rf', 'main_off': True, 'side_mode': 4})


def send(r, op=0, payload=b'', path='direct', sequence=42):
    request = packet(op, sequence, payload)
    return r.receive_rf_hid(request) if path=='rf' else r.receive_raw_hid(request,path)


def play(r, effect='solid', path='direct', **parameters):
    return send(r,5,effect_payload(effect,**parameters),path)


def colors(r):
    return bytes(r.u.mem_read(SIDE,27))


def host(r):
    return bytes(r.u.mem_read(HOST,38))+bytes(r.u.mem_read(HOST+39,13))


class EffectsTests(unittest.TestCase):
    def test_capability_and_atomic_play_all_transports(self):
        for path in PATHS:
            r=runtime(path)
            state=decode(packet(0),send(r,path=path,sequence=0))
            self.assertEqual((state.firmware_version,state.firmware_build),('3.1.0',3))
            self.assertTrue(state.native_effects)
            self.assertEqual(play(r,path=path,color=bytes([255,128,0]),brightness=50)[5],0)
            r.render()
            self.assertEqual(colors(r),bytes([80,40,0])*9)
            self.assertEqual(r.byte(HOST+35),1)
            self.assertFalse(r.flash.operations)

    def test_blink_breathe_and_rainbow_observable_phases(self):
        for path in PATHS:
            r=runtime(path)
            play(r,'blink',path,color=b'\xff\x00\x00',brightness=100,period_ms=1000,duration_ms=3000)
            for ms,expected in [(0,162),(499,162),(500,0),(999,0),(1000,162)]:
                r.now_us=ms*1000;r.render()
                self.assertEqual(colors(r),bytes([expected,0,0])*9,(path,ms))
            play(r,'breathe',path,color=b'\xff\x00\x00',brightness=100,period_ms=1000,request_id=100)
            start=r.now_us
            samples=[]
            for ms in [0,125,250,375,500,625,750,875,1000]:
                r.now_us=start+ms*1000;r.render();samples.append(colors(r)[0])
            self.assertEqual(samples[0],0);self.assertEqual(samples[4],162);self.assertEqual(samples[-1],0)
            self.assertEqual(samples[2],40)
            self.assertTrue(samples[0]<samples[1]<samples[2]<samples[3]<samples[4])
            self.assertTrue(samples[4]>samples[5]>samples[6]>samples[7]>samples[8])
            play(r,'rainbow',path,brightness=100,period_ms=1000,request_id=101)
            start=r.now_us;r.render();first=colors(r)
            self.assertEqual(first[:3],bytes([162,0,0]))
            self.assertEqual(first[9:12],bytes([0,162,0]))
            self.assertEqual(first[18:21],bytes([0,0,162]))
            r.now_us=start+250000;r.render();self.assertNotEqual(colors(r),first)
            r.now_us=start+1000000;r.render();self.assertEqual(colors(r),first)

    def test_no_host_stream_or_idle_counter_resets(self):
        for effect in ['solid','blink','breathe','rainbow']:
            r=runtime('rf')
            play(r,effect,'rf',duration_ms=3000,color=bytes(3) if effect=='rainbow' else b'\xff\x00\x00')
            before=r.boundary_counts.get('rf_tx',0)
            self.assertEqual(before,4)
            r.word(0x80b30,7);r.word(0x80a64,11)
            observed=set()
            for ms in range(0,3001,25):
                r.now_us=ms*1000;r.render();observed.add(colors(r))
                self.assertEqual((r.word(0x80b30),r.word(0x80a64)),(7,11))
            self.assertGreaterEqual(len(observed),1 if effect=='solid' else 2)
            self.assertEqual(r.byte(HOST+35),0)
            self.assertEqual(colors(r),bytes(27))
            self.assertEqual(r.boundary_counts.get('rf_tx',0),before)

    def test_expiry_bounds_clock_wrap_and_final_microsecond(self):
        for path in PATHS:
            for duration in [100,5000,60000]:
                for start in [0,268435000]:
                    r=runtime(path);r.now_us=start
                    play(r,path=path,duration_ms=duration)
                    r.now_us=start+duration*1000-1
                    reply=send(r,path=path)
                    self.assertTrue(reply[8]&1);self.assertEqual(int.from_bytes(reply[15:17],'little'),1)
                    r.now_us+=1;r.render()
                    self.assertFalse(send(r,path=path)[8]&1)
                    self.assertEqual(colors(r),bytes(27))

    def test_duplicate_is_idempotent_before_and_after_expiry_release_and_sleep(self):
        for path in PATHS:
            r=runtime(path);payload=effect_payload('blink',duration_ms=1000,request_id=73)
            send(r,5,payload,path);r.now_us=500000
            self.assertEqual(int.from_bytes(send(r,5,payload,path,sequence=99)[15:17],'little'),500)
            self.assertEqual(r.word(HOST),0)
            changed=bytearray(payload);changed[1]+=1
            before=host(r)
            self.assertEqual(send(r,5,bytes(changed),path)[5],3);self.assertEqual(host(r),before)
            r.now_us=1000000
            self.assertFalse(send(r,5,payload,path)[8]&1)
            replacement=effect_payload('solid',color=b'\x00\xff\x00',request_id=74)
            self.assertTrue(send(r,5,replacement,path)[8]&1)
            send(r,2,path=path)
            self.assertFalse(send(r,5,replacement,path)[8]&1)

    def test_new_effect_replaces_without_queue_and_queries_cannot_extend(self):
        for path in PATHS:
            r=runtime(path);play(r,'rainbow',path,duration_ms=1000)
            r.now_us=500000
            play(r,'solid',path,color=b'\x00\xff\x00',brightness=100,duration_ms=1000)
            r.render();self.assertEqual(colors(r),bytes([0,162,0])*9)
            r.now_us=1000000
            self.assertEqual(send(r,3,path=path)[5],4)
            send(r,4,(10000).to_bytes(2,'little'),path)
            self.assertEqual(int.from_bytes(send(r,path=path)[15:17],'little'),500)
            r.now_us=1500000;r.render();self.assertEqual(colors(r),bytes(27))

    def test_invalid_and_short_play_are_non_mutating(self):
        good=packet(5,42,effect_payload('solid',color=b'\xff\x80\x00',request_id=73))
        invalid=[]
        for offset,value in [(5,0),(5,5),(6,101),(10,1),(14,0),*[(i,1) for i in range(18,32)]]:
            p=bytearray(good);p[offset]=value;invalid.append(bytes(p))
        p=bytearray(good);p[12:14]=(60001).to_bytes(2,'little');invalid.append(bytes(p))
        p=bytearray(good);p[12:14]=bytes(2);invalid.append(bytes(p))
        p=bytearray(good);p[5]=4;p[10:12]=(1000).to_bytes(2,'little');invalid.append(bytes(p))
        for path in PATHS:
            r=runtime(path);play(r,'breathe',path,request_id=90)
            for p in invalid:
                before=host(r)
                reply=r.receive_rf_hid(p) if path=='rf' else r.receive_raw_hid(p,path)
                self.assertEqual(reply[5],3,p.hex());self.assertEqual(host(r),before)
            for length in range(3,32):
                before=host(r)
                reply=r.receive_rf_hid(good[:length]) if path=='rf' else r.receive_raw_hid(good[:length],path)
                self.assertEqual(r.byte(0x80bed),6);self.assertEqual(host(r),before)

    def test_native_parser_write_allowlist_and_no_battery_mutation(self):
        for path in PATHS:
            r=runtime(path);r.byte(0x809b0,32);r.byte(HOST+38,32)
            r.u.mem_write(0x80be8,packet(5,42,effect_payload('rainbow')))
            r.invoke('native_parser_audit',int(MANIFEST['symbols']['side_hid_process'],16))
            allowed=set(range(HOST,HOST+52))|set(range(0x80be8,0x80c08))|set(range(0x9fc00,0xa0000))
            for address,size,_ in r.writes:self.assertTrue(set(range(address,address+size))<=allowed,hex(address))
            self.assertFalse(r.flash.operations)

    def test_native_render_preserves_main_rgb_and_battery_state(self):
        for effect in ['solid','blink','breathe','rainbow']:
            for main in range(19):
                a,b=runtime(),runtime()
                for r in [a,b]:r.apply_environment({'main_off':False,'main_effect':main})
                play(b,effect,color=bytes(3) if effect=='rainbow' else b'\xff\x80\x20')
                battery=bytes(b.u.mem_read(0x80930,0x19))
                for time in [0,250000,500000,1000000]:
                    for r in [a,b]:r.now_us=time;r.render()
                    self.assertEqual(bytes(a.u.mem_read(0x81cc4,252)),bytes(b.u.mem_read(0x81cc4,252)))
                    self.assertEqual(bytes(b.u.mem_read(0x80930,0x19)),battery)

    def test_sleep_cancels_and_wake_does_not_replay(self):
        for gate in ['fast','normal']:
            r=runtime('rf');payload=effect_payload('rainbow',duration_ms=60000,request_id=73)
            send(r,5,payload,'rf');r.render()
            r.sleep_checkpoint(gate)
            self.assertEqual(r.state()['status'],'sleeping')
            self.assertEqual(r.byte(HOST+35),0)
            r.tap(['3,1']);self.assertEqual(r.host_text,'a')
            self.assertFalse(send(r,5,payload,'rf')[8]&1)
            r.render();self.assertEqual(colors(r),bytes(27))

    def test_battery_priority_and_mode_change(self):
        r=runtime();play(r,'solid',color=b'\x00\x00\xff',brightness=100)
        apply_step(r,{'type':'query'});self.assertEqual(r.state()['rgb']['side'],expected_bar(88))
        r.jump_clock(3000);self.assertEqual(colors(r),bytes([0,0,162])*9)
        r.byte(0x80994,1);r.render();self.assertEqual(r.byte(HOST+35),0)

    def test_sleep_trampolines_preserve_live_registers(self):
        for name,end,fast in [('patch_rf_fast_sleep',0x200112b0,True),('patch_rf_normal_sleep',0x20012a52,False)]:
            r=runtime('rf');play(r,'rainbow','rf')
            r.reset_registers();before={i:r.reg(i) for i in range(1,32)}
            r.invoke(name,int(MANIFEST['symbols'][name],16),end=end,check_abi=False)
            for i,value in before.items():
                expected=value-16 if fast and i==2 else 40 if not fast and i==11 else value
                self.assertEqual(r.reg(i),expected,(name,i))
            if fast:self.assertEqual(int.from_bytes(r.u.mem_read(before[2]-12,4),'little'),before[9])

    def test_old_firmware_guard_and_unchanged_adc(self):
        old=(ROOT/'firmware/experimental/side-hid-rf-v3.0.1.bin').read_bytes()
        transport=SimulatedTransport(old,'rf')
        with self.assertRaises(UnsupportedFirmware):Client(transport).play('rainbow')
        self.assertEqual(transport.runtime.calls.get('raw_hid_dispatch'),1)
        self.assertEqual(old[0x117a0:0x11b98],IMAGE[0x117a0:0x11b98])


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--build',type=Path,default=ROOT/'build/side-hid-rf-v3.1.0')
    p.add_argument('--out',type=Path,required=True)
    args=p.parse_args()
    MANIFEST=json.loads((args.build/'build-manifest.json').read_text())
    IMAGE=(args.build/MANIFEST['modified_name']).read_bytes()
    result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(EffectsTests))
    report={'status':'PASS' if result.wasSuccessful() else 'FAIL','tests':result.testsRun,
            'failures':len(result.failures),'errors':len(result.errors),'device_access':'NONE',
            'firmware_sha256':hashlib.sha256(IMAGE).hexdigest(),
            'scope':'Real RV32/Andes code; GPIO, RF delivery and silicon PM are models. No current, voltage or physical refresh measurement.'}
    args.out.parent.mkdir(exist_ok=True,parents=True);args.out.write_text(json.dumps(report,indent=2)+'\n')
    sys.exit(0 if result.wasSuccessful() else 1)
