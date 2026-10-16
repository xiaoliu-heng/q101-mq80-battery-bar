"""Real RV32/Andes execution of HID reception, dispatch and side-light patch.

Hardware boundaries and FIFO are modeled. This is not a physical USB/RF test.
"""
import argparse
import hashlib
import importlib.util
import json
import random
import struct
import sys
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT),str(ROOT/'simulator')]
from controller.protocol import packet,decode,Client,ProtocolError,UnsupportedFirmware
from controller.transports import SimulatedTransport
from mq80lab.engine import Runtime
from mq80lab.peripherals import crc16
from mq80lab.scenarios import BUILTINS,run_scenario,apply_step,expected_bar

BUILD=ROOT/'build/side-hid'
IMAGE=b''
STOCK=(ROOT/'simulator/assets/stock.bin').read_bytes()
V2=(ROOT/'simulator/assets/battery-bar-v2.bin').read_bytes()
HOST=0x8269c
SIDE=0x81cc4+252
FRAME=0x81cc4
checks={}


def usb(image=None,**environment):
    return Runtime(IMAGE if image is None else image,{'connection':'usb','cable':True,**environment})


def host_bytes(r):
    return bytes(r.u.mem_read(HOST,38)) # Exclude recorded USB length and padding.


def send(r,command=0,payload=b'',sequence=42,path='direct'):
    p=packet(command,sequence,payload)
    return decode(p,r.receive_raw_hid(p,path))


def rf_receive(r,request,corrupt_crc=False):
    # Real stock configuration packet: outer CRC covers 41 bytes, payload has
    # a stock additive checksum, and request bytes are copied by 0x1840.
    f=bytearray(64)
    f[5]=7;f[6]=0x57;f[8]=0x8c;f[9]=0xb0;f[10]=len(request)
    f[11:11+len(request)]=request
    f[11+len(request)]=sum(f[9:11+len(request)])&255
    f[47:49]=crc16(f[6:47]).to_bytes(2,'little')
    if corrupt_crc:f[47]^=1
    r.u.mem_write(0x90000,bytes(f))
    r.invoke('rf_configuration_receive',0x1840,0x90000)
    return r.reg(10)


class HidTests(unittest.TestCase):
    def test_image_and_fixed_address_preservation(self):
        manifest=json.loads((BUILD/'build-manifest.json').read_text())
        self.assertEqual(hashlib.sha256(IMAGE).hexdigest(),manifest['modified_sha256'])
        self.assertLess(len(IMAGE),0x20000)
        self.assertEqual(STOCK[0x190:0x91b4],IMAGE[0x190:0x91b4])
        self.assertEqual(STOCK[0x1c000:0x1ed50],IMAGE[0x1c000:0x1ed50])
        allowed={i for p in manifest['patched_ranges'] for i in range(int(p['offset'],16),int(p['offset'],16)+p['length'])}
        self.assertTrue({i for i,(a,b) in enumerate(zip(STOCK[:-4],IMAGE)) if a!=b}<=allowed)
        checks['binary_preservation']={'size':len(IMAGE),'sha256':manifest['modified_sha256'],
                                       'patched_ranges':manifest['patched_ranges']}

    def test_startup_clears_exact_reserved_bss(self):
        r=usb()
        r.u.mem_write(0x809b0,b'\xa5'*(0x826cc-0x809b0))
        r.invoke('startup_bss',0x20000132,end=0x20000152)
        self.assertEqual(bytes(r.u.mem_read(0x809b0,0x826c4-0x809b0)),bytes(0x826c4-0x809b0))
        self.assertEqual(bytes(r.u.mem_read(0x826c4,8)),b'\xa5'*8)

    def test_info_executes_both_real_usb_receivers(self):
        for path in ['direct','general']:
            r=usb();before=host_bytes(r)
            state=send(r,path=path)
            self.assertFalse(state.active)
            self.assertEqual(state.battery_percent,88)
            self.assertEqual(state.connection,'usb')
            self.assertEqual(state.timeout_ms,2000)
            self.assertEqual(host_bytes(r),before)
            self.assertEqual(r.byte(HOST+38),32)
            self.assertFalse(r.flash.operations)

    def test_control_commands_through_both_usb_receivers(self):
        for path in ['direct','general']:
            r=usb()
            self.assertEqual(send(r,4,(750).to_bytes(2,'little'),path=path).timeout_ms,750)
            self.assertTrue(send(r,1,bytes(range(27)),path=path).active)
            r.render()
            self.assertEqual(bytes(r.u.mem_read(SIDE,27)),bytes(c*162//255 for c in range(27)))
            r.jump_clock(500)
            self.assertTrue(send(r,3,path=path).active)
            self.assertEqual(send(r,path=path).remaining_ms,750)
            self.assertFalse(send(r,2,path=path).active)
            r.render()
            self.assertEqual(bytes(r.u.mem_read(SIDE,27)),bytes(27))

    def test_short_reports_cannot_apply_stale_rgb_payload(self):
        for path in ['direct','general']:
            for length in range(1,32):
                r=usb();before=host_bytes(r)
                request=packet(1,73,b'\xff'*27)
                # Direct receiver historically retains the tail of short data.
                r.u.mem_write(0x80be8,request)
                reply=r.receive_raw_hid(request[:length],path)
                if path=='direct' or length>=3:
                    self.assertEqual(reply[5],6,(path,length,reply.hex()))
                else:
                    # General receive zero-pads its local buffer. A prefix
                    # shorter than three bytes is regular unknown VIA traffic.
                    self.assertEqual(reply[0],1)
                self.assertEqual(host_bytes(r),before,(path,length))

    def test_invalid_version_opcode_and_reply_bit_are_non_mutating(self):
        r=usb()
        for byte in [0x01,0x21,0xf1,0x15,0x16,0x17,0x18,0x19,0x1f]:
            p=bytearray(packet(1,91,b'\xff'*27));p[3]=byte
            before=host_bytes(r)
            reply=r.receive_raw_hid(bytes(p))
            self.assertIn(reply[5],[1,2])
            self.assertEqual(host_bytes(r),before)
        self.assertFalse(r.flash.operations)

    def test_non_extension_via_traffic_matches_stock(self):
        commands=[bytes([1]),bytes([2]),bytes([8,3,1]),bytes([8,3,2]),
                  bytes([8,3,3]),bytes([8,3,4]),bytes([0xc]),bytes([0xd]),
                  bytes([0x11]),bytes([0xfe,1,2,3,4])]
        for path in ['direct','general']:
            a,b=usb(V2),usb()
            for data in commands:
                p=data+bytes(32-len(data))
                self.assertEqual(a.receive_raw_hid(p,path),b.receive_raw_hid(p,path),(path,data.hex()))
            self.assertFalse(b.flash.operations)

    def test_full_frame_independent_pixels_and_main_frame_preserved(self):
        rng=random.Random(0x534c)
        for _ in range(80):
            r=usb();baseline=usb()
            rgb=bytes(rng.randrange(256) for _ in range(27))
            state=send(r,1,rgb)
            self.assertTrue(state.active)
            r.render();baseline.render()
            self.assertEqual(bytes(r.u.mem_read(SIDE,27)),bytes(v*162//255 for v in rgb))
            self.assertEqual(bytes(r.u.mem_read(FRAME,252)),bytes(baseline.u.mem_read(FRAME,252)))
            self.assertFalse(r.flash.operations)

    def test_all_channel_values_scale_to_stock_maximum(self):
        r=usb()
        for value in range(256):
            send(r,1,bytes([value])*27);r.render()
            self.assertEqual(bytes(r.u.mem_read(SIDE,27)),bytes([value*162//255])*27)

    def test_original_off_state_restored_by_release_and_watchdog(self):
        for path in ['release','timeout']:
            r=usb(main_off=True,side_mode=4)
            send(r,1,b'\xff'*27);r.render()
            self.assertEqual(bytes(r.u.mem_read(SIDE,27)),b'\xa2'*27)
            if path=='release':send(r,2);r.render()
            else:r.jump_clock(2000)
            self.assertEqual(bytes(r.u.mem_read(SIDE,27)),bytes(27),path)
            self.assertFalse(send(r).active)

    def test_exact_timeout_boundary_and_clock_wrap(self):
        for start in [0,0xffff0000,0xffffffff-16000]:
            r=usb(main_off=True,side_mode=4)
            r.now_us=start//16
            r._next_irq_us=r.now_us+1000
            send(r,1,b'\xff'*27);r.render()
            r.jump_clock(1999)
            self.assertTrue(send(r).active)
            r.jump_clock(1)
            self.assertFalse(send(r).active)
            self.assertEqual(bytes(r.u.mem_read(SIDE,27)),bytes(27))

    def test_info_does_not_renew_and_keepalive_does(self):
        r=usb()
        send(r,1,b'\xff'*27);r.jump_clock(1000)
        self.assertEqual(send(r).remaining_ms,1000)
        r.jump_clock(999);send(r,3)
        r.jump_clock(1500)
        self.assertTrue(send(r).active)
        r.jump_clock(500)
        self.assertFalse(send(r).active)
        reply=r.receive_raw_hid(packet(3))
        self.assertEqual(reply[5],4)

    def test_last_microsecond_remains_active(self):
        r=usb()
        send(r,1,b'\xff'*27)
        r.now_us=1999999
        state=send(r)
        self.assertTrue(state.active)
        self.assertEqual(state.remaining_ms,1)
        r.now_us=2000000
        self.assertFalse(send(r).active)

    def test_dispatch_trampoline_preserves_all_live_registers(self):
        symbols=json.loads((BUILD/'build-manifest.json').read_text())['symbols']
        for claimed in [False,True]:
            r=usb()
            r.u.mem_write(0x80be8,packet(0) if claimed else bytes(32))
            r.byte(HOST+38,32)
            r.reset_registers()
            before={i:r.reg(i) for i in range(1,32)}
            r.invoke('dispatch_trampoline',int(symbols['patch_hid_dispatch_entry'],16),
                     end=0x2000e2f4 if claimed else 0x2000e230,check_abi=False)
            for register,value in before.items():
                if register!=9:self.assertEqual(r.reg(register),value,(claimed,register))
            self.assertEqual(r.reg(9),0x80be8)

    def test_hid_parser_writes_only_reply_state_and_stack(self):
        symbols=json.loads((BUILD/'build-manifest.json').read_text())['symbols']
        r=usb()
        r.u.mem_write(0x80be8,packet(1,73,bytes(range(27))))
        r.byte(HOST+38,32)
        boundaries=dict(r.boundary_counts)
        r.invoke('parser_memory_audit',int(symbols['side_hid_process'],16))
        allowed=set(range(0x80be8,0x80c08))|set(range(HOST,HOST+40))|set(range(0x9fc00,0xa0000))
        for address,size,_ in r.writes:
            self.assertTrue(set(range(address,address+size))<=allowed,(hex(address),size))
        self.assertEqual(r.boundary_counts,boundaries)
        self.assertFalse(r.flash.operations)

    def test_client_sequence_wrap(self):
        t=SimulatedTransport(IMAGE);c=Client(t)
        c.info()
        for i in range(260):
            state=c.frame(bytes([i&255])*27)
            self.assertEqual(state.last_frame_sequence,(i+2)&255)
        self.assertFalse(t.runtime.flash.operations)

    def test_timeout_configuration_bounds(self):
        r=usb()
        for duration in [500,2000,10000]:
            self.assertEqual(send(r,4,duration.to_bytes(2,'little')).timeout_ms,duration)
        before=host_bytes(r)
        for duration in [0,499,10001,65535]:
            reply=r.receive_raw_hid(packet(4,payload=duration.to_bytes(2,'little')))
            self.assertEqual(reply[5],3)
            self.assertEqual(host_bytes(r),before)

    def test_fn_b_temporarily_overrides_then_resumes_host_frame(self):
        for side_mode in [0,4]:
            r=usb(main_off=True,side_mode=side_mode)
            send(r,4,(5000).to_bytes(2,'little'))
            send(r,1,bytes([0,0,255])*9);r.render()
            apply_step(r,{'type':'query'})
            self.assertTrue(send(r).battery_query)
            self.assertEqual(r.state()['rgb']['side'],expected_bar(88))
            r.jump_clock(3000)
            self.assertFalse(send(r).battery_query)
            self.assertEqual(bytes(r.u.mem_read(SIDE,27)),bytes([0,0,162])*9)

    def test_host_timeout_during_query_preserves_battery_then_stock(self):
        r=usb(main_off=True,side_mode=4)
        send(r,1,b'\xff'*27)
        apply_step(r,{'type':'query'})
        r.jump_clock(2000)
        self.assertFalse(send(r).active)
        self.assertEqual(r.state()['rgb']['side'],expected_bar(88))
        r.jump_clock(1100)
        self.assertEqual(bytes(r.u.mem_read(SIDE,27)),bytes(27))

    def test_connection_switch_revokes_host_control(self):
        r=usb(main_off=True,side_mode=4)
        send(r,1,b'\xff'*27);r.render()
        r.byte(0x80994,1);r.render()
        self.assertEqual(r.byte(HOST+35),0)
        self.assertEqual(bytes(r.u.mem_read(SIDE,27)),bytes(27))

    def test_real_rf_receive_dispatch_reply_and_control_gate(self):
        r=Runtime(IMAGE,{'connection':'rf'})
        p=packet(0,37)
        self.assertEqual(rf_receive(r,p),1)
        self.assertEqual(bytes(r.u.mem_read(0x80be8,32)),p)
        r.invoke('raw_hid_dispatch',0x2000e190)
        reply=bytes(r.u.mem_read(0x80be8,32))
        self.assertEqual(decode(p,reply).connection,'rf')
        self.assertEqual(r.byte(0x80b2d),2)
        r.invoke('rf_configuration_reply',0x20010dfc)
        rf_packet=bytes.fromhex(r.packets[-1]['bytes'])
        self.assertEqual(rf_packet[12:44],reply)
        self.assertEqual(rf_packet[44],sum(rf_packet[10:44])&255)
        checks['rf_keyboard_path']={'receive_entry':'0x1840','dispatcher':'0x2000e190',
                                    'reply_entry':'0x20010dfc','request':p.hex(),
                                    'reply':reply.hex(),'rf_packet':rf_packet.hex(),
                                    'receiver_firmware_emulated':False,'physical_rf_test':False}
        r.byte(0x80b2d,0)
        before=host_bytes(r)
        self.assertEqual(rf_receive(r,packet(1,38,b'\xff'*27)),1)
        r.invoke('raw_hid_dispatch',0x2000e190)
        self.assertEqual(r.byte(0x80be8+5),5)
        self.assertEqual(host_bytes(r),before)

    def test_bad_rf_crc_is_rejected_before_dispatch(self):
        r=Runtime(IMAGE,{'connection':'rf'})
        self.assertEqual(rf_receive(r,packet(0),corrupt_crc=True),0)
        self.assertEqual(r.byte(0x80b2d),0)

    def test_host_client_refuses_stock_before_any_control_write(self):
        for image in [STOCK,V2]:
            t=SimulatedTransport(image)
            with self.assertRaises(UnsupportedFirmware):Client(t).frame(b'\xff'*27)
            self.assertEqual(t.runtime.calls.get('raw_hid_dispatch'),1)
            self.assertFalse(t.runtime.flash.operations)

    def test_guarded_host_client_commands(self):
        t=SimulatedTransport(IMAGE);c=Client(t)
        self.assertFalse(c.info().active)
        self.assertEqual(c.timeout(700).timeout_ms,700)
        self.assertTrue(c.frame(bytes(range(27))).active)
        t.elapse(.4)
        self.assertTrue(c.keepalive().active)
        self.assertFalse(c.release().active)
        with self.assertRaises(ValueError):c.frame(bytes(26))
        with self.assertRaises(ValueError):c.timeout(499)

    def test_typing_and_battery_regression_all_builtin_scenarios(self):
        results=[]
        for case in BUILTINS:
            with self.subTest(case=case['id']):
                _,result=run_scenario(IMAGE,case)
                self.assertEqual(result['status'],'PASS',result['error'])
                results.append({'id':case['id'],'status':result['status']})
        checks['existing_scenarios']=results

    def test_usb_typing_while_host_frames_arrive(self):
        r=usb();send(r,1,b'\xff'*27)
        r.set_keys(['4,0','3,1']);r.advance(30)
        send(r,1,bytes(range(27)));r.advance(10)
        r.set_keys([]);r.advance(30)
        self.assertEqual(r.host_text,'A')
        self.assertEqual(r.last_host_hid,bytes(8))


def main():
    global BUILD,IMAGE
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build',type=Path,default=BUILD)
    parser.add_argument('--out',type=Path)
    parser.add_argument('--battery-regression',action='store_true')
    args=parser.parse_args()
    BUILD=args.build.resolve()
    manifest=json.loads((BUILD/'build-manifest.json').read_text())
    IMAGE=(BUILD/manifest['modified_name']).read_bytes()
    result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(HidTests))
    report={'status':'PASS_OFFLINE_NOT_FLASHED' if result.wasSuccessful() else 'FAIL',
            'tests_run':result.testsRun,'failures':len(result.failures),'errors':len(result.errors),
            'modified_sha256':hashlib.sha256(IMAGE).hexdigest(),'checks':checks,
            'device_access':'NONE','physical_usb_validated':False,'physical_rf_validated':False}
    if result.wasSuccessful() and args.battery_regression:
        spec=importlib.util.spec_from_file_location('battery_validation',ROOT/'firmware/source/validate_offline.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        symbols={k:int(v,16) for k,v in manifest['symbols'].items()}
        battery={}
        tests=[('fn_b_dispatch',lambda:module.check_query(STOCK,IMAGE,symbols)),
               ('bar_machine_code',lambda:module.check_bar(IMAGE)),
               ('stock_renderer',lambda:module.check_original_render(STOCK,IMAGE)),
               ('expiry_restore',lambda:module.check_expiry(STOCK,IMAGE)),
               ('total_frame',lambda:module.check_total_frame(STOCK,IMAGE)),
               ('rgb_encoder',lambda:module.check_encoded_output(IMAGE))]
        try:
            for name,test in tests:
                print('Battery regression:',name,flush=True)
                battery[name]=test()
                # That field describes the battery-only trampoline, not this wrapper.
                battery[name].pop('added_stack_depth_bytes',None)
            report['checks']['battery_regression']=battery
        except Exception as error:
            report['status']='FAIL';report['battery_error']=repr(error)
    path=args.out or BUILD/'hid-validation.json'
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    if report['status']=='FAIL':raise SystemExit(1)


if __name__=='__main__':main()
