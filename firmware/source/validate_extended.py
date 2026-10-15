"""Additional offline-only review: startup, deadline crossing and stateful frames.

No device APIs. The clock boundary model deliberately advances between reads;
the original validation used a frozen timer for each individual function call.
"""
import argparse
import hashlib
import importlib.util
import json
import random
from pathlib import Path

spec = importlib.util.spec_from_file_location('machine', Path(__file__).with_name('validate_offline.py'))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

SCENARIOS = [
    ('normal', {}), ('side_disabled', {0x80993: 0}),
    ('connection_1', {0x809f0: 1}), ('connection_2', {0x809f0: 2}),
    ('connection_3', {0x809f0: 3}), ('reset', {0x80bba: 1}),
    ('low_battery', {0x80b90: 1}), ('primary_decoration', {0x808cc: 1}),
    ('all_lighting_disabled', {0x808b2: 0, 0x808cd: 0, 0x80993: 0}),
    ('tick_gate', {0x80be3: 7})]


class ClockSequence(m.FirmwareEmulator):
    def __init__(self, image):
        super().__init__(image)
        self.times = []
        self.timer_reads = 0
        self.u.mem_protect(m.XIP, 0x20000, m.un.UC_PROT_READ | m.un.UC_PROT_EXEC)

    def set_times(self, *times):
        self.times = list(times)
        self.timer_reads = 0

    def on_read(self, u, kind, address, size, value, data):
        if address == m.STIMER and self.times:
            self.clock(self.times[min(self.timer_reads, len(self.times) - 1)])
            self.timer_reads += 1
        super().on_read(u, kind, address, size, value, data)


def initialize(e, side=0, connection=0, charge=0, effect=0, extra=None):
    fields = {m.PERCENT: 88, m.FLAGS: 0, m.OUTPUT: 2, m.MODE: connection,
              0x80b44: charge, 0x80be3: 10, 0x809f0: 0, 0x80bba: 0,
              0x80b90: 0, 0x80993: 1, 0x808b1: effect, 0x808b2: 1,
              0x808cc: 0, 0x808ca: side, 0x808cd: 3, 0x808cf: 1,
              0x808d0: 3, 0x80ba1: 20, 0x80ba2: 255, 0x80ba3: 60,
              0x80b45: 10, 0x80b79: 5, 0x80b8e: 64}
    fields.update(extra or {})
    for address, value in fields.items():
        e.byte(address, value)
    e.u.mem_write(m.FRAME, bytes((i * 37 + 23) % 256 for i in range(279)))
    e.u.mem_write(0x8269c, b'\xa7' * 32)


def call_frame(e, sp=0xa0000):
    e.reset_registers()
    e.u.reg_write(m.REG[2], sp)
    e.call(m.XIP + 0xc400)
    assert e.u.mem_read(0x8269c, 32) == b'\xa7' * 32, 'BSS end guard'
    for address, size, value in e.writes:
        assert (0x80000 <= address and address + size <= 0x8269c) or (
            sp - 2048 <= address and address + size <= sp), ('unexpected write', hex(address), size)
    return max((sp - a for a, _, _ in e.writes if sp - 2048 <= a < sp), default=0)


def stock_reference(stock, patched, expired=False):
    e = ClockSequence(stock)
    e.u.mem_write(0x80000, bytes(patched.u.mem_read(0x80000, 0x26bc)))
    e.byte(m.FLAGS, e.byte(m.FLAGS) & ~2)
    if expired:
        e.byte(0x80aa2, 0)
        e.byte(0x80b45, 0)
        e.byte(0x80b8e, 64)
        e.u.mem_write(m.SIDE, bytes(27))
    return e


def check_full_startup_copies(stock, modified):
    snapshots = []
    for image, end in [(stock, 0x82698), (modified, 0x8269c)]:
        e = ClockSequence(image)
        e.u.mem_write(0, b'\xa5' * m.RAM_END)
        e.call(m.XIP + 0x8e, end=m.XIP + 0x174)
        for address, offset, length in [(0, 0x190, 0x158), (0x178, 0x2e8, 0xfc4),
                                         (0x1200, 0x12b0, 0x7f04), (0x80000, 0x1e3a0, 0x9b0)]:
            assert e.u.mem_read(address, length) == image[offset:offset + length]
        assert e.u.mem_read(0x158, 0x20) == bytes(0x20)
        assert e.u.mem_read(0x809b0, end - 0x809b0) == bytes(end - 0x809b0)
        assert e.u.mem_read(end, m.RAM_END - end) == b'\xa5' * (m.RAM_END - end)
        snapshots.append(bytes(e.u.mem_read(0, m.RAM_END)))
    differences = [i for i, (a, b) in enumerate(zip(*snapshots)) if a != b]
    assert differences == list(range(0x82698, 0x8269c))
    return {'status': 'PASS', 'actual_startup_copy_and_zero_loops': True,
            'only_ram_difference': '0x82698..0x8269b',
            'not_executed': 'reset CSR/peripheral setup and main application startup'}


def check_moving_deadline(stock, modified):
    count = 0
    for start in [0, 0xffffff00]:
        for side in range(5):
            for connection in range(3):
                for charge in range(3):
                    for name, extra in SCENARIOS:
                        e = ClockSequence(modified)
                        initialize(e, side, connection, charge, extra=extra)
                        e.word(m.TICK, start)
                        e.byte(m.FLAGS, 2)
                        e.set_times(start + m.VIEW - 1, start + m.VIEW + 1)
                        call_frame(e)
                        assert e.timer_reads == 1, ('multiple time decisions', name)
                        assert e.byte(m.FLAGS) & 2, ('expired within a frame', name)
                        assert bytes(e.u.mem_read(m.SIDE, 27)) == m.expected_bar(88)
                        # On the next frame the deadline has elapsed. Compare to
                        # native rendering after removal of the temporary state.
                        e.byte(0x80be3, extra.get(0x80be3, 10))
                        ref = stock_reference(stock, e, expired=True)
                        e.set_times(start + m.VIEW + 100)
                        call_frame(ref)
                        call_frame(e)
                        assert not e.byte(m.FLAGS) & 2
                        assert e.u.mem_read(0x80000, 0x269c) == ref.u.mem_read(0x80000, 0x269c), (name, side, connection, charge)
                        count += 1
    return {'status': 'PASS', 'two_frame_deadline_scenarios': count,
            'timer_wrap_included': True, 'all_side_modes_connections_charge_states': True,
            'one_deadline_decision_per_frame': True, 'next_frame_restores_stock_data_and_off_flags': True}


def check_primary_effects(stock, modified):
    count = 0
    depths = {'stock': 0, 'modified': 0}
    for effect in range(19):
        for side in range(5):
            for state in ['inactive', 'active', 'expired']:
                e = ClockSequence(modified)
                initialize(e, side=side, effect=effect)
                e.word(m.TICK, 0xffff0000)
                e.set_times(0xffff0000 + (m.VIEW if state == 'expired' else 100))
                if state != 'inactive':
                    e.byte(m.FLAGS, 2)
                if state == 'expired':
                    e.u.mem_write(m.SIDE, m.expected_bar(88))
                ref = stock_reference(stock, e, expired=state == 'expired')
                depths['stock'] = max(depths['stock'], call_frame(ref))
                depths['modified'] = max(depths['modified'], call_frame(e))
                assert e.u.mem_read(m.FRAME, 252) == ref.u.mem_read(m.FRAME, 252), ('primary frame', effect, side, state)
                if state == 'active':
                    assert e.u.mem_read(m.SIDE, 27) == m.expected_bar(88)
                else:
                    assert e.u.mem_read(0x80000, 0x269c) == ref.u.mem_read(0x80000, 0x269c), ('data state', effect, side, state)
                count += 1
    return {'status': 'PASS', 'cases': count, 'primary_effect_ids': list(range(19)),
            'side_modes': 5, 'states': 3, 'observed_stack_write_depth_bytes': depths,
            'scope': 'controlled initial states; not every animation history or physical interrupt schedule'}


def check_stateful_frames(stock, modified):
    rng = random.Random(0x20261015)
    counts = {'frames': 0, 'active': 0, 'inactive_or_expired': 0, 'presses': 0,
              'releases': 0, 'encoded_frames': 0}
    for session in range(10):
        e = ClockSequence(modified)
        initialize(e, side=session % 5, connection=session % 3, charge=session % 3,
                   effect=session % 19)
        now = 0xffff0000 if session % 2 else 0x12340000
        start = None
        for frame in range(100):
            now = (now + (m.VIEW + 1 if frame % 29 == 0 else rng.randrange(10000, 2000001))) & 0xffffffff
            e.times = []
            if frame in [0, 7, 28, 45, 55, 88]:
                m.query(e, 1, now)
                start = now
                counts['presses'] += 1
            if frame in [1, 8, 29, 46, 56, 89]:
                # The event producer sets the press/release input before dispatch.
                e.byte(0x80a6e, 0)
                before = bytes(e.u.mem_read(0x80000, 0x269c))
                m.query(e, 0, now)
                assert before == e.u.mem_read(0x80000, 0x269c)
                counts['releases'] += 1
            e.byte(m.PERCENT, rng.randrange(256))
            e.byte(0x80993, 0 if frame % 13 < 3 else 1)
            e.byte(0x80be3, 10)
            e.byte(0x80b8e, (e.byte(0x80b8e) + 2) % 256)
            e.byte(0x80ba3, (e.byte(0x80ba3) + 2) % 256)
            e.byte(0x80b45, (e.byte(0x80b45) + 2) % 256)
            active = start is not None and (now - start) & 0xffffffff < m.VIEW
            expired = bool(e.byte(m.FLAGS) & 2) and not active
            ref = stock_reference(stock, e, expired=expired)
            e.set_times(now)
            sp = [0xa0000, 0x9f000, 0x90000][frame % 3]
            call_frame(ref, sp)
            call_frame(e, sp)
            assert bool(e.byte(m.FLAGS) & 2) == active
            assert e.u.mem_read(m.FRAME, 252) == ref.u.mem_read(m.FRAME, 252)
            if active:
                assert e.u.mem_read(m.SIDE, 27) == m.expected_bar(e.byte(m.PERCENT))
                counts['active'] += 1
            else:
                assert e.u.mem_read(0x80000, 0x269c) == ref.u.mem_read(0x80000, 0x269c)
                counts['inactive_or_expired'] += 1
                start = None
            if frame % 10 == 0:
                saved_tick = e.word(m.TICK)
                e.reset_registers()
                e.call(m.XIP + 0x15f58)
                assert e.word(m.TICK) == saved_tick
                assert e.u.mem_read(0x8269c, 32) == b'\xa7' * 32
                counts['encoded_frames'] += 1
            counts['frames'] += 1
    return {'status': 'PASS', **counts, 'timer_wrap_retrigger_release_and_on_off_toggles': True,
            'three_stack_locations': True, 'flash_XIP_read_execute_only': True}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--build', type=Path, required=True)
    ap.add_argument('--out', type=Path, required=True)
    args = ap.parse_args()
    manifest = json.loads((args.build / 'build-manifest.json').read_text())
    stock = (args.build / 'MQ80-CS836B-V0109-20241220-STOCK.bin').read_bytes()
    modified = (args.build / manifest['modified_name']).read_bytes()
    result = {'status': 'RUNNING', 'modified_sha256': hashlib.sha256(modified).hexdigest(),
              'device_access': 'NONE', 'checks': {}}
    for name, fn in [('startup_copies_and_bss', check_full_startup_copies),
                     ('moving_deadline', check_moving_deadline),
                     ('all_primary_effects', check_primary_effects),
                     ('stateful_frames', check_stateful_frames)]:
        print('Checking', name, flush=True)
        result['checks'][name] = fn(stock, modified)
        args.out.write_text(json.dumps(result, indent=2) + '\n')
        print(name, 'PASS', flush=True)
    result['status'] = 'PASS_OFFLINE_ONLY_NOT_FLASHED'
    args.out.write_text(json.dumps(result, indent=2) + '\n')


if __name__ == '__main__':
    main()
