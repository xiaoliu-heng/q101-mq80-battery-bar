"""Additional pre-flash checks on the exact BIN; no physical device access.

The independent RV32 runner executes the new parser without Unicorn. Other
groups execute the original firmware with modeled peripheral boundaries.
Virtual stream time is not a physical throughput or power measurement.
"""
import argparse
import hashlib
import importlib.util
import json
import random
import struct
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / 'simulator')]
from controller.protocol import packet
from mq80lab.engine import Runtime
from mq80lab.peripherals import crc16
from mq80lab.ota import run_ota

HOST, RAW, SIDE = 0x8269c, 0x80be8, 0x81dc0
END, TIMER = 0x826c4, 0x80140200


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


independent = module('independent_rv32', ROOT / 'firmware/source/validate_patch_rv32.py')


class ParserRunner(independent.Runner):
    """Memory extension of the existing independent standard-library CPU.

    The CPU decoder is unchanged; the new parser also needs its fixed BSS,
    stock reply buffer and a bounded stack. All stores have an allowlist.
    """
    def __init__(self, image, seed=0):
        super().__init__(image, seed)
        self.ram = bytearray(0x20000)
        self.allowed = (set(range(RAW, RAW + 32)) | set(range(HOST, END)) |
                        set(range(0x9fc00, 0xa0000)) |
                        set(range(0x80b30, 0x80b34)) | set(range(0x80a64, 0x80a68)))

    def load(self, address, size):
        if 0x80000 <= address and address + size <= 0xa0000:
            return int.from_bytes(self.ram[address-0x80000:address-0x80000+size], 'little')
        return super().load(address, size)

    def store(self, address, size, value):
        assert set(range(address, address + size)) <= self.allowed, ('store', hex(address), size)
        assert 0x80000 <= address and address + size <= 0xa0000
        self.put(address, (value & ((1 << (size * 8)) - 1)).to_bytes(size, 'little'))
        self.writes.append((address, size))

    def put(self, address, data):
        start = address - 0x80000
        self.ram[start:start+len(data)] = data

    def get(self, address, size):
        return bytes(self.ram[address-0x80000:address-0x80000+size])


def independent_parser(image, symbols):
    e = ParserRunner(image, 0x534c)
    entry = symbols['side_hid_process']
    count = 0
    maximum = 0
    stack_depth = 0

    def request(data, mode=2, length=32):
        nonlocal count, maximum, stack_depth
        e.set_byte(0x80994, mode)
        e.set_byte(HOST+38, length)
        e.set_byte(0x809b0, length)
        e.put(RAW, data)
        e.writes = []
        maximum = max(maximum, e.run(entry))
        stack_depth = max(stack_depth, max((0xa0000-a for a, _ in e.writes if a >= 0x9fc00), default=0))
        count += 1
        return e.get(RAW, 32)

    request(packet(1, 77, bytes(range(27))))
    # Full byte-space validation, including stale buffer contents and live
    # control. An invalid request must never modify the lease, colors or idle.
    invalid = 0
    for mode in range(4):
        for length in range(256):
            if length == 32 and mode in [1, 2]:
                continue
            e.set_byte(HOST+38, length)
            before = e.get(HOST, 40), e.get(0x80b30, 4), e.get(0x80a64, 4)
            reply = request(packet(1, 99, b'\xff'*27), mode, length)
            assert reply[5] == (5 if length == 32 else 6), (mode, length, reply.hex())
            assert before == (e.get(HOST, 40), e.get(0x80b30, 4), e.get(0x80a64, 4))
            invalid += 1
    for mode in [1, 2]:
        e.set_byte(HOST+38, 32)
        for opcode in range(256):
            if 0x10 <= opcode <= 0x14:
                continue
            data = bytearray(packet(1, 99, b'\xff'*27)); data[3] = opcode
            before = e.get(HOST, 40)
            assert request(bytes(data), mode)[5] in [1, 2]
            assert e.get(HOST, 40) == before
            invalid += 1
    namespace = 0
    for index in range(3):
        for value in range(256):
            data = bytearray(packet(1, 99, b'\xff'*27))
            if value == data[index]:
                continue
            data[index] = value
            before = e.get(HOST, 40)
            assert request(bytes(data)) == bytes(data)
            assert e.regs[10] == 0 and e.get(HOST, 40) == before
            namespace += 1
    boundaries = 0
    for mode in [1, 2]:
        for start in [0, 1, 0xfffffff0, 0xffff0000]:
            for timeout in [500, 501, 2000, 9999, 10000]:
                e.put(HOST, bytes(40)); e.now = start
                assert request(packet(4, payload=timeout.to_bytes(2, 'little')), mode)[5] == 0
                assert request(packet(1, 19, b'\xa5'*27), mode)[8] & 1
                for elapsed, active in [(timeout*16000-1, True), (timeout*16000, False)]:
                    e.now = start + elapsed
                    reply = request(packet(0), mode)
                    assert bool(reply[8] & 1) == active
                    assert int.from_bytes(reply[15:17], 'little') == int(active)
                    boundaries += 1
                # This reproduced the v3.0.0 bug without Unicorn or a renderer.
                reply = request(packet(4, payload=(10000).to_bytes(2, 'little')), mode)
                assert not reply[8] & 1
                assert request(packet(3), mode)[5] == 4
    rng = random.Random(0x20261017)
    timeouts = [0, 1, 499, 500, 501, 9999, 10000, 10001, 32767, 32768, 65535]
    timeouts += [rng.randrange(65536) for _ in range(128)]
    for milliseconds in timeouts:
        before = e.get(HOST, 40)
        reply = request(packet(4, payload=milliseconds.to_bytes(2, 'little')))
        assert reply[5] == (0 if 500 <= milliseconds <= 10000 else 3)
        if reply[5]:
            assert e.get(HOST, 40) == before
    return {'status': 'PASS', 'engine': 'independent standard-library RV32IM interpreter',
            'parser_calls': count, 'invalid_length_mode_opcode_cases': invalid,
            'non_extension_magic_cases': namespace, 'exact_tick_boundaries': boundaries,
            'timeout_values': len(timeouts), 'max_parser_instructions': maximum,
            'observed_parser_stack_bytes': stack_depth, 'store_allowlist_and_abi': True}


def startup_and_render(image):
    ext = module('extended', ROOT / 'firmware/source/validate_extended.py')
    base = (ROOT / 'simulator/assets/battery-bar-v2.bin').read_bytes()
    stock = (ROOT / 'simulator/assets/stock.bin').read_bytes()
    for data, end in [(stock, 0x82698), (base, 0x8269c), (image, END)]:
        e = ext.ClockSequence(data)
        e.u.mem_write(0, b'\xa5' * 0xa0000)
        e.call(0x2000008e, end=0x20000174)
        for address, offset, length in [(0,0x190,0x158),(0x178,0x2e8,0xfc4),
                                        (0x1200,0x12b0,0x7f04),(0x80000,0x1e3a0,0x9b0)]:
            assert e.u.mem_read(address, length) == data[offset:offset+length]
        assert e.u.mem_read(0x809b0, end-0x809b0) == bytes(end-0x809b0)
        assert e.u.mem_read(end, 0xa0000-end) == b'\xa5'*(0xa0000-end)
    cases = 0
    depth = 0
    for effect in range(19):
        for side in range(5):
            for mode in range(3):
                for query in [False, True, 'expired']:
                    a, b = ext.ClockSequence(base), ext.ClockSequence(image)
                    for e in [a, b]:
                        ext.initialize(e, side=side, connection=mode, effect=effect)
                        e.u.mem_write(HOST, bytes(40))
                        e.u.mem_write(END, b'\xa7'*32)
                        e.word(0x82698, 0xffff0000)
                        if query:
                            e.byte(0x80b58, 2)
                        e.set_times(0xffff0000 + (48000000 if query == 'expired' else 100))
                        e.reset_registers();e.call(0x2000c400)
                        assert e.u.mem_read(END, 32) == b'\xa7'*32
                    # With host lighting inactive, every original data byte
                    # must still match v2, not merely a visual screenshot.
                    assert a.u.mem_read(0x80000, HOST-0x80000) == b.u.mem_read(0x80000, HOST-0x80000), (effect,side,mode,query)
                    depth = max(depth, max((0xa0000-x for x,n,v in b.writes if 0x9f800 <= x < 0xa0000), default=0))
                    cases += 1
    return {'status':'PASS','startup_images':3,'render_comparisons_to_v2':cases,
            'main_effects':19,'side_modes':5,'connections':3,'battery_query_states':3,
            'full_original_data_compared':True,'observed_frame_stack_bytes':depth,
            'limitations':['Controlled states, not full boot or arbitrary interrupt schedules.']}


def rf_corruption(image):
    r = Runtime(image, {'connection':'rf','cable':False})
    request = packet(1, 123, bytes(range(27)))
    frame = bytearray(64)
    frame[5]=7;frame[6]=0x57;frame[8]=0x8c;frame[9]=0xb0;frame[10]=32
    frame[11:43]=request;frame[43]=sum(frame[9:43])&255
    frame[47:49]=crc16(frame[6:47]).to_bytes(2,'little')
    before = bytes(r.u.mem_read(HOST,40)), bytes(r.u.mem_read(RAW,32))
    count = 0
    for index in range(6,49):
        for bit in range(8):
            damaged = bytearray(frame);damaged[index] ^= 1 << bit
            r.u.mem_write(0x90000, bytes(damaged))
            r.invoke('corrupt_rf_frame',0x1840,0x90000)
            assert r.reg(10) == 0 and r.byte(0x80b2d) == 0, (index,bit)
            assert before == (bytes(r.u.mem_read(HOST,40)),bytes(r.u.mem_read(RAW,32)))
            count += 1
    assert not r.flash.operations
    return {'status':'PASS','single_bit_corruptions_rejected':count,
            'scope':'Stock receive RAM envelope, not physical RF packets.'}


def expiry_regression(image):
    previous=(ROOT/'firmware/experimental/side-hid-rf-v3.0.0.bin').read_bytes()
    rows=[]
    for path in ['direct','general','rf']:
        for case in ['expired','shortened']:
            observed=[]
            for data in [previous,image]:
                r=Runtime(data,{'connection':'rf' if path=='rf' else 'usb','cable':path!='rf'})
                exchange=r.receive_rf_hid if path=='rf' else lambda p:r.receive_raw_hid(p,path)
                exchange(packet(1,payload=b'\xff'*27))
                r.now_us=2000000 if case=='expired' else 750000
                if case=='shortened':exchange(packet(4,payload=(500).to_bytes(2,'little')))
                assert not exchange(packet(0))[8]&1
                reply=exchange(packet(4,payload=(5000).to_bytes(2,'little')))
                observed.append(bool(reply[8]&1))
            assert observed == [True,False], (path,case,observed)
            rows.append({'path':path,'case':case,'v3.0.0_reactivates_expired_frame':observed[0],
                         'candidate_reactivates_expired_frame':observed[1]})
    return {'status':'PASS','rows':rows,
            'previous_sha256':hashlib.sha256(previous).hexdigest(),
            'note':'Only a new FRAME can restart control after expiry; an adjusted TIMEOUT cannot.'}


def stream_soak(image, seconds):
    rows = []
    for mode, fps in [('usb',20), ('rf',10)]:
        r = Runtime(image, {'connection':mode,'cable':mode=='usb','side_mode':4,'main_off':True})
        exchange = r.receive_raw_hid if mode=='usb' else r.receive_rf_hid
        exchange(packet(4,payload=(500).to_bytes(2,'little')))
        start = r.now_us
        frames = fps*seconds+1
        for i in range(frames):
            r.now_us = start + i*(1000000//fps)
            colors = bytes((i*73+j*17)&255 for j in range(27))
            reply = exchange(packet(1,i&255,colors))
            assert reply[5] == 0 and reply[8]&1 and reply[11] == i&255
            r.render()
            assert bytes(r.u.mem_read(SIDE,27)) == bytes(c*162//255 for c in colors)
            assert r.byte(0x80b2d) == 0
            if mode == 'rf':
                assert r.word(0x80b30) == r.word(0x80a64) == 0
        r.now_us += 500000
        r.render()
        assert not exchange(packet(0))[8]&1
        assert exchange(packet(3))[5] == 4
        assert bytes(r.u.mem_read(SIDE,27)) == bytes(27)
        assert not r.flash.operations
        rows.append({'connection':mode,'status':'PASS','virtual_seconds':seconds,
                     'frames':frames,'target_fps':fps,'sequence_wraps':(frames-1)//256,
                     'timer_wraps':((start+seconds*1000000)*16)//(1<<32)-(start*16)//(1<<32),
                     'stopped_sender_expired':True,'flash_operations':0})
        print('Stream PASS', mode, frames, flush=True)
    return {'status':'PASS','rows':rows,
            'scope':'Packet transactions and rendering at modeled timestamps; idle IRQs and radio timing are not simulated here.'}


def ota_worker(args):
    return run_ota(*args)


def ota_matrix(image):
    images = [(ROOT/'simulator/assets'/name).read_bytes() for name in ['stock.bin','battery-bar-v2.bin']]
    jobs = [(running,candidate,scenario,bank) for old in images
            for running,candidate in [(old,image),(image,old)]
            for bank in [0,0x40000]
            for scenario in ['valid_image','incomplete','crc_error','header_readback_error']]
    with ProcessPoolExecutor(max_workers=4) as pool:
        rows = list(pool.map(ota_worker, jobs))
    assert all(row['status']=='PASS' for row in rows)
    return {'status':'PASS_OFFLINE_ONLY','count':len(rows),'passed':len(rows),'rows':rows}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--build',type=Path,required=True)
    p.add_argument('--out',type=Path,required=True)
    p.add_argument('--group',choices=['core','soak','ota'],default='core')
    p.add_argument('--seconds',type=int,default=300)
    args=p.parse_args()
    assert args.seconds >= 269, 'Soak must cross the 32-bit 16 MHz timer wrap'
    manifest=json.loads((args.build/'build-manifest.json').read_text())
    image=(args.build/manifest['modified_name']).read_bytes()
    digest=hashlib.sha256(image).hexdigest()
    assert digest==manifest['modified_sha256']
    report={'status':'RUNNING','firmware_sha256':digest,'group':args.group,
            'firmware_version':manifest['firmware_version'],'firmware_build':manifest['firmware_build'],
            'device_access':'NONE','checks':{}}
    args.out.parent.mkdir(parents=True,exist_ok=True)
    start=time.monotonic()
    def save():args.out.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    save()
    try:
        if args.group=='core':
            symbols={k:int(v,16) for k,v in manifest['symbols'].items()}
            for name,fn in [('independent_parser',lambda:independent_parser(image,symbols)),
                            ('startup_and_render',lambda:startup_and_render(image)),
                            ('rf_corruption',lambda:rf_corruption(image)),
                            ('expiry_regression',lambda:expiry_regression(image))]:
                report['checks'][name]=fn();save();print(name,'PASS',flush=True)
        elif args.group=='soak':report['checks']['continuous_stream']=stream_soak(image,args.seconds)
        else:report['checks']['ota']=ota_matrix(image)
        report['status']='PASS_OFFLINE_ONLY_NOT_FLASHED'
    except Exception as error:
        report['status']='FAIL';report['error']=repr(error);raise
    finally:
        report['wall_seconds']=round(time.monotonic()-start,3);save()


if __name__=='__main__':main()
