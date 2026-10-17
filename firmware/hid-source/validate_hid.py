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

BUILD=ROOT/'build/side-hid-rf-v3.0.1'
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
    return bytes(r.u.mem_read(HOST,38))+bytes(r.u.mem_read(HOST+39,1)) # Exclude recorded USB length.


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
    def test_firmware_version_features_and_build_through_usb_and_rf(self):
        for path in ['direct','general','rf']:
            r=Runtime(IMAGE,{'connection':'rf' if path=='rf' else 'usb','cable':path!='rf'})
            before=host_bytes(r)
            p=packet(0,101)
            reply=r.receive_rf_hid(p) if path=='rf' else r.receive_raw_hid(p,path)
            state=decode(p,reply)
            self.assertEqual(state.firmware_version,'3.0.1')
            self.assertEqual(state.firmware_build,2)
            self.assertEqual(state.firmware_feature_flags,15)
            self.assertEqual(state.firmware_features,('fn_b_battery_bar','battery_query','usb_side_rgb','rf_side_rgb'))
            self.assertEqual(reply[23:],b'FV\x01\x03\x00\x01\x0f\x02\x00')
            self.assertEqual(host_bytes(r),before)
            self.assertFalse(r.flash.operations)
        checks['firmware_identity']={'version':state.firmware_version,'build':state.firmware_build,
                                     'features':state.firmware_features,'protocol_version':1,
                                     'transports':['usb-direct','usb-general','rf']}

    def test_new_version_tail_preserves_all_existing_v1_reply_fields(self):
        previous=(ROOT/'firmware/experimental/side-hid-rf.bin').read_bytes()
        for connection in ['usb','rf']:
            a,b=[SimulatedTransport(image,connection) for image in [previous,IMAGE]]
            for command,payload in [(0,b''),(4,(750).to_bytes(2,'little')),(1,bytes(range(27))),(3,b''),(2,b'')]:
                p=packet(command,55,payload);qa=a.request(p);qb=b.request(p)
                self.assertEqual(qa[:23],qb[:23],(connection,command))
                self.assertEqual(qa[23:],bytes(9))
                self.assertEqual(qb[23:26],b'FV\x01')

    def test_old_hid_versions_remain_explicitly_unknown_and_controllable(self):
        for filename,connection in [('side-hid.bin','usb'),('side-hid-rf.bin','rf')]:
            t=SimulatedTransport((ROOT/'firmware/experimental'/filename).read_bytes(),connection)
            c=Client(t);state=c.info()
            self.assertIsNone(state.firmware_version)
            self.assertIsNone(state.firmware_build)
            self.assertIsNone(state.firmware_feature_flags)
            self.assertEqual(state.firmware_features,())
            self.assertTrue(c.frame(b'\xff'*27).active)
            self.assertFalse(c.release().active)

    def test_unknown_metadata_schema_is_not_misidentified(self):
        r=usb();p=packet(0);reply=bytearray(r.receive_raw_hid(p))
        for marker in [b'FV\x02',b'??\x01',bytes(3)]:
            reply[23:26]=marker
            state=decode(p,bytes(reply))
            self.assertIsNone(state.firmware_version)
            self.assertIsNone(state.firmware_build)
            self.assertTrue(state.usb_control)

    def test_feature_flags_and_build_are_decoded_without_guessing(self):
        r=usb();p=packet(0);reply=bytearray(r.receive_raw_hid(p))
        reply[26:29]=bytes([3,2,4]);reply[29]=0x81;reply[30:32]=(513).to_bytes(2,'little')
        state=decode(p,bytes(reply))
        self.assertEqual(state.firmware_version,'3.2.4')
        self.assertEqual(state.firmware_build,513)
        self.assertEqual(state.firmware_feature_flags,0x81)
        self.assertEqual(state.firmware_features,('fn_b_battery_bar',))

    def test_manifest_constant_and_versioned_filename_match_firmware(self):
        manifest=json.loads((BUILD/'build-manifest.json').read_text())
        r=usb();state=send(r)
        self.assertEqual(manifest['firmware_version'],state.firmware_version)
        self.assertEqual(manifest['firmware_build'],state.firmware_build)
        self.assertEqual(manifest['firmware_feature_flags'],state.firmware_feature_flags)
        self.assertIn('-v3.0.1-',manifest['modified_name'])
        offset=int(manifest['symbols']['side_firmware_metadata'],16)-0x20000000
        self.assertEqual(IMAGE[offset:offset+9],r.receive_raw_hid(packet(0))[23:])
        self.assertEqual(IMAGE[:0x18],STOCK[:0x18])

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

    def test_timeout_change_cannot_revive_expired_frame_before_next_render(self):
        # The main loop can receive TIMEOUT after the deadline but before the
        # lighting task has cleared active. No render/jump_clock is allowed
        # between expiry and the request: that was the missing v3.0.0 case.
        for path in ['direct','general','rf']:
            for elapsed_us in [2000000,2000001]:
                for start_us in [0,268435000]:
                    r=Runtime(IMAGE,{'connection':'rf' if path=='rf' else 'usb',
                                     'cable':path!='rf','main_off':True,'side_mode':4})
                    exchange=r.receive_rf_hid if path=='rf' else lambda p:r.receive_raw_hid(p,path)
                    r.now_us=start_us
                    exchange(packet(1,17,b'\xff'*27));r.render()
                    r.now_us+=elapsed_us
                    self.assertFalse(exchange(packet(0))[8]&1)
                    r.word(0x80b30,7);r.word(0x80a64,11)
                    response=exchange(packet(4,payload=(5000).to_bytes(2,'little')))
                    self.assertEqual(response[5],0)
                    self.assertFalse(response[8]&1,(path,elapsed_us,start_us))
                    self.assertEqual(exchange(packet(3))[5],4)
                    r.render()
                    self.assertEqual(bytes(r.u.mem_read(SIDE,27)),bytes(27))
                    self.assertEqual((r.word(0x80b30),r.word(0x80a64)),(7,11))
                    self.assertTrue(exchange(packet(1,payload=b'\x80'*27))[8]&1)

    def test_shortening_timeout_latches_expiry_until_new_frame(self):
        for path in ['direct','general','rf']:
            r=Runtime(IMAGE,{'connection':'rf' if path=='rf' else 'usb','cable':path!='rf'})
            exchange=r.receive_rf_hid if path=='rf' else lambda p:r.receive_raw_hid(p,path)
            exchange(packet(1,payload=b'\xff'*27))
            r.now_us=750000
            self.assertFalse(exchange(packet(4,payload=(500).to_bytes(2,'little')))[8]&1)
            # Extending a newly expired timeout in the same main-loop interval
            # must not reactivate the old frame either.
            self.assertFalse(exchange(packet(4,payload=(5000).to_bytes(2,'little')))[8]&1)
            self.assertEqual(exchange(packet(3))[5],4)
            self.assertTrue(exchange(packet(1,payload=b'\x80'*27))[8]&1)

    def test_live_timeout_change_keeps_original_frame_timestamp(self):
        for path in ['direct','general','rf']:
            r=Runtime(IMAGE,{'connection':'rf' if path=='rf' else 'usb','cable':path!='rf'})
            exchange=r.receive_rf_hid if path=='rf' else lambda p:r.receive_raw_hid(p,path)
            exchange(packet(1,payload=b'\xff'*27))
            r.now_us=250000
            response=exchange(packet(4,payload=(500).to_bytes(2,'little')))
            self.assertTrue(response[8]&1)
            self.assertEqual(int.from_bytes(response[15:17],'little'),250)
            self.assertEqual(r.word(HOST),0)
            response=exchange(packet(4,payload=(5000).to_bytes(2,'little')))
            self.assertTrue(response[8]&1)
            self.assertEqual(int.from_bytes(response[15:17],'little'),4750)
            self.assertEqual(r.word(HOST),0)

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

    def test_real_rf_receive_dispatch_reply_and_control(self):
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
        self.assertEqual(rf_receive(r,packet(1,38,b'\xff'*27)),1)
        r.invoke('raw_hid_dispatch',0x2000e190)
        self.assertEqual(r.byte(0x80be8+5),0)
        self.assertEqual(r.byte(HOST+39),1)
        r.render()
        self.assertEqual(bytes(r.u.mem_read(SIDE,27)),b'\xa2'*27)

    def test_rf_all_commands_and_repeated_transaction_cleanup(self):
        t=SimulatedTransport(IMAGE,connection='rf');c=Client(t);r=t.runtime
        self.assertTrue(c.info().rf_control)
        self.assertEqual(c.timeout(750).timeout_ms,750)
        for i in range(260):
            state=c.frame(bytes([i&255])*27)
            self.assertEqual(state.last_frame_sequence,c.sequence)
            self.assertEqual(r.byte(0x80b2d),0)
        self.assertEqual(len(r.packets),1000)
        r.jump_clock(500)
        self.assertTrue(c.keepalive().active)
        self.assertEqual(c.info().remaining_ms,750)
        self.assertFalse(c.release().active)
        self.assertFalse(r.flash.operations)
        self.assertGreater(r.calls['rf_configuration_closing_ack'],780)

    def test_rf_bad_crc_checksum_and_short_frame_are_non_mutating(self):
        r=Runtime(IMAGE,{'connection':'rf','cable':False})
        before=host_bytes(r)
        for corrupt in ['corrupt_crc','corrupt_checksum']:
            self.assertIsNone(r.receive_rf_hid(packet(1,payload=b'\xff'*27),**{corrupt:True}))
            self.assertEqual(host_bytes(r),before)
            self.assertEqual(r.byte(0x80b2d),0)
        for length in range(3,32):
            reply=r.receive_rf_hid(packet(1,payload=b'\xff'*27)[:length])
            self.assertEqual(len(reply),length)
            self.assertEqual(r.byte(0x80be8+5),6)
            self.assertEqual(host_bytes(r),before)

    def test_rf_live_lease_holds_idle_then_expiry_and_release_allow_sleep(self):
        for finish in ['release','timeout']:
            t=SimulatedTransport(IMAGE,connection='rf');c=Client(t);r=t.runtime
            for command in ['frame','keepalive']:
                if command=='frame':c.frame(b'\xff'*27)
                r.word(0x80b30,90);r.word(0x80a64,590)
                if command=='keepalive':c.keepalive()
                r.render()
                self.assertEqual(r.word(0x80b30),0)
                self.assertEqual(r.word(0x80a64),0)
            if finish=='release':c.release()
            else:r.jump_clock(2000)
            r.word(0x80b30,7);r.word(0x80a64,11)
            self.assertFalse(c.info().active)
            r.render()
            self.assertEqual(r.word(0x80b30),7)
            self.assertEqual(r.word(0x80a64),11)
            r.sleep_checkpoint()
            self.assertEqual(r.state()['status'],'sleeping')
            r.tap(['3,1'])
            self.assertEqual(r.host_text,'a')

    def test_rf_info_and_invalid_keepalive_cannot_delay_sleep(self):
        r=Runtime(IMAGE,{'connection':'rf','cable':False})
        r.word(0x80b30,7);r.word(0x80a64,11)
        self.assertFalse(decode(packet(0),r.receive_rf_hid(packet(0))).active)
        self.assertEqual(r.receive_rf_hid(packet(3))[5],4)
        self.assertEqual(r.word(0x80b30),7)
        self.assertEqual(r.word(0x80a64),11)

    def test_rf_parser_only_adds_two_idle_counters_to_memory_write_set(self):
        r=Runtime(IMAGE,{'connection':'rf','cable':False})
        symbols=json.loads((BUILD/'build-manifest.json').read_text())['symbols']
        r.u.mem_write(0x80be8,packet(1,73,bytes(range(27))))
        r.byte(0x809b0,32)
        r.invoke('rf_parser_memory_audit',int(symbols['side_hid_process'],16))
        allowed=(set(range(0x80be8,0x80c08))|set(range(HOST,HOST+40))|
                 set(range(0x9fc00,0xa0000))|set(range(0x80b30,0x80b34))|set(range(0x80a64,0x80a68)))
        for address,size,_ in r.writes:
            self.assertTrue(set(range(address,address+size))<=allowed,(hex(address),size))
        self.assertFalse(r.flash.operations)

    def test_rf_connection_switch_revokes_instead_of_migrating_lease(self):
        t=SimulatedTransport(IMAGE,connection='rf');c=Client(t);r=t.runtime
        c.frame(bytes([0,0,255])*9);r.render()
        self.assertEqual(bytes(r.u.mem_read(SIDE,27)),bytes([0,0,162])*9)
        r.byte(0x80994,2);r.render()
        self.assertEqual(r.byte(HOST+35),0)
        # USB connection indication can legitimately render after a mode
        # switch; verify host RGB is no longer reapplied across render calls.
        r.render()
        self.assertNotEqual(bytes(r.u.mem_read(SIDE,27)),bytes([0,0,162])*9)

    def test_rf_non_extension_via_traffic_matches_battery_firmware(self):
        a,b=[Runtime(image,{'connection':'rf','cable':False}) for image in [V2,IMAGE]]
        for data in [bytes([1]),bytes([2]),bytes([8,3,1]),bytes([0xc]),bytes([0xfe,1,2,3,4])]:
            p=data+bytes(32-len(data))
            self.assertEqual(a.receive_rf_hid(p),b.receive_rf_hid(p),p.hex())
        self.assertFalse(b.flash.operations)

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

    def test_client_refuses_rf_control_on_legacy_usb_only_firmware(self):
        legacy=(ROOT/'firmware/experimental/side-hid.bin').read_bytes()
        t=SimulatedTransport(legacy,connection='rf');c=Client(t)
        with self.assertRaises(ProtocolError):c.frame(b'\xff'*27)
        self.assertEqual(t.runtime.calls.get('raw_hid_dispatch'),1)
        self.assertEqual(t.runtime.byte(HOST+35),0)
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
