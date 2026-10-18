"""Audit RF lighting and the stock battery estimator offline; never opens HID.

Execute the original machine code with explicit ADC/register fixtures. This
checks software paths and memory writes, not current draw or real voltage sag.
Run with the repository venv and --out <report.json>.
"""
import argparse
import colorsys
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / 'simulator')]
from mq80lab import machine
from mq80lab.engine import Runtime
from controller.protocol import packet

ADC_TASK = 0x200117a0
ADC_TIMER = 0x80a74
PERCENT = 0x80948
FILTERED = 0x80933
FILTER_COUNT = 0x80a10
WARMUP_COUNT = 0x809d5
PERIOD_TICKS = 0x03d09000


class AuditRuntime(Runtime):
    def __init__(self, image):
        self.raw_adc = 3369
        self.adc_calls = 0
        self.converted = []
        self.protected_writes = []
        self.rf_packet_types = []
        super().__init__(image, {'connection': 'rf', 'cable': False})

    def on_code(self, u, address, size, extra):
        if address == ADC_TASK:
            self.adc_calls += 1
        if address == 0x20011940:
            self.converted.append({'scaled_adc': self.reg(13),
                                   'target_percent': self.reg(11)})
        super().on_code(u, address, size, extra)

    def on_read(self, u, kind, address, size, value, extra):
        if address == 0x80140184 and size == 2:
            # A deterministic ADC result, not a model of the battery circuit.
            u.mem_write(address, self.raw_adc.to_bytes(2, 'little'))
        super().on_read(u, kind, address, size, value, extra)

    def record_write(self, address, size, value):
        regions = [(0x638, 0x642), (0x80930, 0x80949),
                   (FILTER_COUNT, FILTER_COUNT + 1),
                   (WARMUP_COUNT, WARMUP_COUNT + 1),
                   (0x80a4c, 0x80a4e), (ADC_TIMER, ADC_TIMER + 4),
                   (0x80140180, 0x80140190)]
        if any(address < end and address + size > start
               for start, end in regions):
            self.protected_writes.append({
                'task': (self.call_context or {}).get('name'),
                'pc': hex(self.u.reg_read(machine.PC)),
                'address': hex(address), 'size': size, 'value': value})

    def on_write(self, u, kind, address, size, value, extra):
        self.record_write(address, size, value)
        super().on_write(u, kind, address, size, value, extra)

    def write_int(self, address, value, size):
        # Andes GP stores use this adapter rather than Unicorn's store hook.
        self.record_write(address, size, value)
        super().write_int(address, value, size)

    def _rf_transmit(self, address):
        self.rf_packet_types.append(self.byte(address + 10))
        super()._rf_transmit(address)


def fixture(image, warmup=6, percent=80):
    r = AuditRuntime(image)
    # Execute the stock initialization blocks rather than inventing limits.
    r.invoke('init_adc_prescale', 0x4218, end=0x4228, check_abi=False)
    r.invoke('init_adc_multiplier', 0x427c, end=0x4284, check_abi=False)
    r.invoke('init_adc_limits', 0x42f4, end=0x430c,
             registers={20: 0x178}, check_abi=False)
    assert bytes(r.u.mem_read(0x80a4c, 2)) == bytes([4, 1])
    assert int.from_bytes(r.u.mem_read(0x63c, 2), 'little') == 2035
    assert int.from_bytes(r.u.mem_read(0x63e, 2), 'little') == 1510
    r.byte(WARMUP_COUNT, warmup)
    r.byte(FILTERED, percent)
    r.byte(PERCENT, percent)
    r.byte(FILTER_COUNT, 0)
    r.now_us = 10_000_000
    r.word(ADC_TIMER, r.now_us * 16)
    r.protected_writes.clear()
    return r


def set_scaled_fixture(r, value):
    reference = int.from_bytes(r.u.mem_read(0x638, 2), 'little')
    r.raw_adc = round(value * 8192 / (reference * 4))


def sample_row(r, timestamp):
    return {'time_seconds': round(timestamp / 1e6, 6),
            'raw_adc_fixture': r.raw_adc, **r.converted[-1],
            'filtered_percent': r.byte(FILTERED),
            'reported_percent': r.byte(PERCENT),
            'filter_count': r.byte(FILTER_COUNT),
            'warmup_count': r.byte(WARMUP_COUNT)}


def scheduled_sample(r, value):
    set_scaled_fixture(r, value)
    elapsed = ((r.now_us * 16) - r.word(ADC_TIMER)) & 0xffffffff
    wait_us = max(0, (PERIOD_TICKS - elapsed) // 16 + 1000)
    r.now_us += wait_us
    before = r.adc_calls
    timestamp = r.now_us
    # Exact original main-loop predicate, timer update and ADC call.
    r.invoke('adc_schedule_fragment', 0x454c, end=0x4570,
             registers={8: 0x80140000, 18: ADC_TIMER}, check_abi=False)
    assert r.adc_calls == before + 1
    return sample_row(r, timestamp)


def estimator_cases(image):
    steady = fixture(image)
    rows = []
    for value in [1932] * 2 + [1840] * 10 + [1932] * 5:
        rows.append(scheduled_sample(steady, value))
    assert [r['reported_percent'] for r in rows[:2]] == [80, 80]
    assert rows[6]['reported_percent'] == 79
    assert rows[11]['reported_percent'] == 78
    assert all(r['reported_percent'] == 78 for r in rows[12:])
    # Positive control: both store adapters must observe real estimator writes.
    addresses = {w['address'] for w in steady.protected_writes}
    assert {hex(ADC_TIMER), hex(PERCENT), hex(FILTER_COUNT)} <= addresses

    intermittent = fixture(image)
    intermittent_rows = [scheduled_sample(intermittent, value)
                         for value in [1840, 1932] * 5]
    assert intermittent_rows[-1]['reported_percent'] == 79

    startup = fixture(image, warmup=0, percent=99)
    set_scaled_fixture(startup, 2000)
    timestamp = startup.now_us
    # The stock boot calls ADC once directly before entering the main loop.
    # Execute that function, followed by its actual scheduling basic block.
    startup.invoke('boot_adc_sample', ADC_TASK)
    startup_rows = [sample_row(startup, timestamp)]
    startup_rows += [scheduled_sample(startup, value)
                     for value in [1990, 1980, 1970, 1960, 1950, 1950]]
    assert [r['reported_percent'] for r in startup_rows[:5]] == [92, 91, 89, 87, 85]
    deltas = [round(b['time_seconds'] - a['time_seconds'], 6)
              for a, b in zip(startup_rows, startup_rows[1:])]
    assert deltas == [0.081168, 0.081168, 2.001168, 2.001168, 2.001168, 4.001]
    return {'steady_and_recovery': rows,
            'intermittent_low_samples': intermittent_rows,
            'startup_warmup': startup_rows,
            'startup_sample_intervals_seconds': deltas,
            'scope': 'Actual ADC conversion, filtering and main-loop scheduling '
                     'basic block, not a full boot/main-loop simulation. ADC '
                     'values, initial state and GPIO are fixtures. Times are '
                     'virtual; instruction execution and radio airtime are not timed.'}


def stream_case(image, brightness, frames):
    r = fixture(image)
    set_scaled_fixture(r, 1932)
    r.receive_rf_hid(packet(0, 0))
    r.receive_rf_hid(packet(4, 1, (2000).to_bytes(2, 'little')))
    assert not r.protected_writes, r.protected_writes
    before_tx = r.boundary_counts.get('rf_tx', 0)
    before_packet = len(r.rf_packet_types)
    before_instructions = r.total_instructions
    start = r.now_us
    for index in range(frames):
        r.now_us = start + index * 100_000
        rgb = []
        for led in range(9):
            pixel = colorsys.hsv_to_rgb((led / 9 + index / 50) % 1, 1, 1)
            rgb.extend(round(channel * 255) * brightness // 100
                       for channel in pixel)
        reply = r.receive_rf_hid(packet(1, (index + 2) & 255, bytes(rgb)))
        assert reply[9] == 80
        r.render()
        r.invoke('adc_schedule_fragment', 0x454c, end=0x4570,
                 registers={8: 0x80140000, 18: ADC_TIMER}, check_abi=False)
        assert r.byte(PERCENT) == r.byte(FILTERED) == 80
    writes = [w for w in r.protected_writes
              if w['task'] != 'adc_schedule_fragment']
    assert not writes, writes
    tx = r.boundary_counts.get('rf_tx', 0) - before_tx
    assert tx == frames * 4
    types = r.rf_packet_types[before_packet:]
    assert types == [0x30, 0xb7, 0xb7, 0xb7] * frames
    return {'brightness': brightness, 'rgb_frames': frames,
            'target_frame_interval_ms': 100,
            'keyboard_rf_transmit_submissions': tx,
            'submissions_per_rgb_frame': tx / frames,
            'transmit_packet_types': {hex(k): v for k, v in Counter(types).items()},
            'original_adc_task_calls': r.adc_calls,
            'instructions_including_modeled_boundary_wrappers':
                r.total_instructions - before_instructions,
            'protected_writes_from_rf_or_rendering': writes,
            'initial_percent': 80, 'final_percent': r.byte(PERCENT),
            'adc_fixture': r.raw_adc,
            'scope': 'Fixed injected ADC input. RF delivery/ACKs are immediate '
                     'models; power, retries and PHY airtime are not modeled.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--frames', type=int, default=100)
    args = parser.parse_args()
    assert 1 <= args.frames <= 600
    stock = (ROOT / 'simulator/assets/stock.bin').read_bytes()
    image = (ROOT / 'firmware/experimental/side-hid-rf-v3.0.1.bin').read_bytes()
    assert stock[0x117a0:0x11b98] == image[0x117a0:0x11b98]
    assert stock[0x454c+0xb0:0x4570+0xb0] == image[0x454c+0xb0:0x4570+0xb0]
    original_estimator = estimator_cases(stock)
    patched_estimator = estimator_cases(image)
    assert original_estimator == patched_estimator
    streams = [stream_case(image, brightness, args.frames)
               for brightness in [25, 65]]
    result = {
        'status': 'PASS_CODE_AUDIT_HARDWARE_CAUSE_UNCONFIRMED',
        'device_access': 'NONE', 'firmware_sha256': hashlib.sha256(image).hexdigest(),
        'stock_firmware_sha256': hashlib.sha256(stock).hexdigest(),
        'audit_script_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'adc_function_sha256': hashlib.sha256(image[0x117a0:0x11b98]).hexdigest(),
        'unchanged_stock_adc_code': True,
        'unchanged_stock_sampling_predicate': True,
        'stock_sampling': {'timer_address': hex(ADC_TIMER),
                           'idle_seconds_address': '0x80a64',
                           'period_ticks': PERIOD_TICKS,
                           'ticks_per_second': 16000000,
                           'steady_period_seconds': PERIOD_TICKS / 16000000,
                           'startup_can_override_timer': True},
        'estimator': patched_estimator, 'rf_rainbow_streams': streams,
        'findings': [
            'The protected estimator state was not written by tested RF '
            'transactions or host RGB rendering.',
            'One RGB update submitted one 0x30 response plus three 0xB7 '
            'closing packets at the keyboard RF TX boundary.',
            'Constant ADC fixtures held the reported percentage constant '
            'while 10 FPS rainbow frames were processed.',
            'The original estimator accumulated low samples even when higher '
            'samples occurred between them; recovery did not raise the cache.',
            'Startup samples can directly lower the reported percent much '
            'faster than the steady filter.'],
        'not_proven': [
            'Actual current, voltage, lost capacity or electrical root cause.',
            'The physical calibration, ADC values or estimator startup state '
            'of the user keyboard.',
            'Actual RF retransmission rate, airtime or sample/radio overlap.']}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({'status': result['status'], 'device_access': 'NONE',
                      'stream_frames': args.frames * len(streams),
                      'report': str(args.out)}))


if __name__ == '__main__':
    main()
