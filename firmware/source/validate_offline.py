"""Execute the actual patched RV32 machine code in Unicorn, with RAM/MMIO mocks.

No HID/USB/Bluetooth/radio access. The small Andes extension adapter executes
the stock instructions reached by the renderer and Fn+B paths. Unknown
instructions fail the run. This is not a full-device or RF simulation.

Usage: python3 validate_offline.py --build BUILD --out validation.json
Dependencies: unicorn==2.1.4. Other checks use only Python's standard library.
"""
import argparse
import hashlib
import json
import random
import struct
import zlib
from pathlib import Path

import unicorn as un
from unicorn import riscv_const as rv

XIP = 0x20000000
RAM_END = 0xa0000
STOP = 0x40000000
REG = [getattr(rv, "UC_RISCV_REG_X%d" % i) for i in range(32)]
PC = rv.UC_RISCV_REG_PC
CALLEE = [3, 4, 8, 9, *range(18, 28)]
FRAME = 0x81cc4
SIDE = FRAME + 84 * 3
PERCENT = 0x80948
FLAGS = 0x80b58
OUTPUT = 0x80b57
MODE = 0x80994
TICK = 0x82698
STIMER = 0x80140200
VIEW = 48000000


def bits(w, lo, hi):
    return (w >> lo) & ((1 << (hi - lo + 1)) - 1)


def signed(v, width):
    return v - (1 << width) if v & (1 << (width - 1)) else v


def h(data):
    return hashlib.sha256(data).hexdigest()


class FirmwareEmulator:
    def __init__(self, image):
        self.image = image
        self.u = un.Uc(un.UC_ARCH_RISCV, un.UC_MODE_RISCV32)
        self.u.mem_map(0, RAM_END)
        self.u.mem_map(XIP, 0x20000)
        self.u.mem_map(STOP, 0x1000)
        self.u.mem_map(0x80140000, 0x4000)
        self.u.mem_write(XIP, image)
        self.u.mem_write(0, image[0x190:0x2e8])
        self.u.mem_write(0x178, image[0x2e8:0x12ac])
        self.u.mem_write(0x1200, image[0x12b0:0x91b4])
        self.u.mem_write(0x80000, image[0x1e3a0:0x1ed50])
        self.writes = []
        self.reads = []
        self.visited = set()
        self.error = None
        self.extension_count = 0
        self.instruction_count = 0
        self.pending_extension = None
        self.u.hook_add(un.UC_HOOK_CODE, self.on_code)
        self.u.hook_add(un.UC_HOOK_INSN_INVALID, self.on_invalid)
        self.u.hook_add(un.UC_HOOK_MEM_WRITE, self.on_write)
        self.u.hook_add(un.UC_HOOK_MEM_READ, self.on_read)
        self.reset_registers()

    def reset_registers(self):
        for i in range(1, 32):
            self.u.reg_write(REG[i], (0x13579000 + i * 0x1357) & 0xffffffff)
        self.u.reg_write(REG[1], STOP)
        self.u.reg_write(REG[2], RAM_END)
        self.u.reg_write(REG[3], 0x80800)

    def on_code(self, u, address, size, _):
        self.visited.add(address)
        self.instruction_count += 1
        if self.instruction_count > 100000:
            self.error = "Execution exceeded the offline instruction budget"
            u.emu_stop()
            return
        half = int.from_bytes(u.mem_read(address, 2), "little")
        if half & 3 == 3:
            word = int.from_bytes(u.mem_read(address, 4), "little")
            if word & 0x7f in [0x0b, 0x2b, 0x5b, 0x7f]:
                self.pending_extension = (word, address, 4)
                u.emu_stop()
        elif (half & 0xe083) == 0x8000:
            index = ((bits(half, 8, 8) << 11) | (bits(half, 12, 12) << 10)
                     | (bits(half, 3, 3) << 9) | (bits(half, 9, 9) << 8)
                     | (bits(half, 5, 6) << 6) | (bits(half, 2, 2) << 5)
                     | (bits(half, 10, 11) << 3) | (bits(half, 4, 4) << 2))
            assert index < 0x158
            word = int.from_bytes(u.mem_read(index, 4), "little")
            self.pending_extension = (word, address, 2)
            u.emu_stop()

    def on_write(self, u, _, address, size, value, __):
        self.writes.append((address, size, value & ((1 << (size * 8)) - 1)))

    def on_read(self, u, _, address, size, value, __):
        self.reads.append((address, size))

    def read_int(self, address, size, sign=False):
        self.reads.append((address, size))
        return int.from_bytes(self.u.mem_read(address, size), "little", signed=sign)

    def write_int(self, address, value, size):
        value &= (1 << (size * 8)) - 1
        self.writes.append((address, size, value))
        self.u.mem_write(address, value.to_bytes(size, "little"))

    def reg(self, index):
        return self.u.reg_read(REG[index]) if index else 0

    def set_reg(self, index, value):
        if index:
            self.u.reg_write(REG[index], value & 0xffffffff)

    def execute_extension(self, w, pc):
        opc, f = w & 0x7f, bits(w, 12, 14)
        rd, rs1, rs2 = bits(w, 7, 11), bits(w, 15, 19), bits(w, 20, 24)
        gp = self.reg(3)
        if opc == 0x7f and f == 1 and bits(w, 25, 31) == 0x62:
            # Andes V5 DSP MADDR32: Rd += low32(Rs1 * Rs2). The stock
            # primary-frame clear uses it for base + LED index * 3.
            # Semantics: Nuclei's official NMSIS MADDR32 intrinsic reference.
            self.set_reg(rd, self.reg(rd) + self.reg(rs1) * self.reg(rs2))
            return pc + 4
        if opc == 0x0b:
            f &= 3
            if f == 3:
                v = ((bits(w, 31, 31) << 17) | (bits(w, 15, 16) << 15)
                     | (bits(w, 17, 19) << 12) | (bits(w, 7, 7) << 11)
                     | (bits(w, 25, 30) << 5) | (bits(w, 8, 11) << 1) | bits(w, 14, 14))
            else:
                v = ((bits(w, 31, 31) << 17) | (bits(w, 15, 16) << 15)
                     | (bits(w, 17, 19) << 12) | (bits(w, 20, 20) << 11)
                     | (bits(w, 21, 30) << 1) | bits(w, 14, 14))
            address = (gp + signed(v, 18)) & 0xffffffff
            if f == 3:
                self.write_int(address, self.reg(rs2), 1)
            elif f == 1:
                self.set_reg(rd, address)
            else:
                self.set_reg(rd, self.read_int(address, 1, f == 0))
            return pc + 4
        if opc == 0x2b and f in [0, 1, 2, 4, 5, 6]:
            store, word = f in [0, 4], f in [2, 4, 6]
            v = ((bits(w, 31, 31) << (18 if word else 17)) | (bits(w, 15, 16) << 15)
                 | (bits(w, 17, 19) << 12))
            if store:
                v |= (bits(w, 7, 7) << 11) | (bits(w, 25, 30) << 5)
                v |= ((bits(w, 8, 8) << 17) | (bits(w, 9, 11) << 2)) if word else (bits(w, 8, 11) << 1)
            else:
                v |= bits(w, 20, 20) << 11
                v |= ((bits(w, 21, 21) << 17) | (bits(w, 22, 30) << 2)) if word else (bits(w, 21, 30) << 1)
            address = (gp + signed(v, 19 if word else 18)) & 0xffffffff
            n = 4 if word else 2
            if store:
                self.write_int(address, self.reg(rs2), n)
            else:
                self.set_reg(rd, self.read_int(address, n, f == 1))
            return pc + 4
        if opc == 0x5b:
            if f in [2, 3]:
                msb, lsb = bits(w, 26, 31), bits(w, 20, 25)
                value = self.reg(rs1)
                if msb == 0:
                    value = signed(value & 1, 1) if f == 3 else (value & 1)
                    value <<= lsb
                elif msb < lsb:
                    n = lsb - msb + 1
                    value &= (1 << n) - 1
                    value = signed(value, n) if f == 3 else value
                    value <<= msb
                else:
                    n = msb - lsb + 1
                    value = (value >> lsb) & ((1 << n) - 1)
                    value = signed(value, n) if f == 3 else value
                self.set_reg(rd, value)
                return pc + 4
            kind = bits(w, 25, 31)
            if f == 0 and 5 <= kind <= 11:
                value = self.reg(rs2)
                if kind >= 8:
                    value &= 0xffffffff
                shift = kind - 4 if kind <= 7 else kind - 8
                self.set_reg(rd, self.reg(rs1) + (value << shift))
                return pc + 4
            if f in [5, 6, 7]:
                d = signed((bits(w, 31, 31) << 10) | (bits(w, 25, 29) << 5) | (bits(w, 8, 11) << 1), 11)
                if f == 7:
                    bit = (bits(w, 7, 7) << 5) | bits(w, 20, 24)
                    is_set = bool(self.reg(rs1) & (1 << bit))
                    taken = is_set if bits(w, 30, 30) else not is_set
                else:
                    immediate = (bits(w, 30, 30) << 6) | (bits(w, 7, 7) << 5) | bits(w, 20, 24)
                    equal = self.reg(rs1) == immediate
                    taken = equal if f == 5 else not equal
                return pc + d if taken else pc + 4
        raise AssertionError("Unmodeled stock instruction 0x%08x at 0x%08x" % (w, pc))

    def on_invalid(self, u, _):
        pc = u.reg_read(PC)
        try:
            half = int.from_bytes(u.mem_read(pc, 2), "little")
            if (half & 3) != 3 and (half & 0xe083) == 0x8000:
                index = ((bits(half, 8, 8) << 11) | (bits(half, 12, 12) << 10)
                         | (bits(half, 3, 3) << 9) | (bits(half, 9, 9) << 8)
                         | (bits(half, 5, 6) << 6) | (bits(half, 2, 2) << 5)
                         | (bits(half, 10, 11) << 3) | (bits(half, 4, 4) << 2))
                assert index < 0x158
                w = int.from_bytes(u.mem_read(index, 4), "little")
                # This path's EXEC.IT entries are register/memory operations.
                next_pc = self.execute_extension(w, pc)
                assert next_pc == pc + 4, "EXEC.IT control-flow needs explicit semantics"
                next_pc = pc + 2
            else:
                assert half & 3 == 3
                w = int.from_bytes(u.mem_read(pc, 4), "little")
                next_pc = self.execute_extension(w, pc)
            u.reg_write(PC, next_pc & 0xffffffff)
            self.extension_count += 1
            self.visited.add(pc)
            return True
        except Exception as e:
            self.error = str(e)
            return False

    def byte(self, address, value=None):
        if value is not None:
            self.u.mem_write(address, bytes([value & 255]))
        return self.u.mem_read(address, 1)[0]

    def clock(self, value):
        self.u.mem_write(STIMER, struct.pack("<I", value & 0xffffffff))

    def word(self, address, value=None):
        if value is not None:
            self.u.mem_write(address, struct.pack("<I", value & 0xffffffff))
        return struct.unpack("<I", self.u.mem_read(address, 4))[0]

    def call(self, address, end=STOP):
        before = {i: self.reg(i) for i in [1, 2, *CALLEE]}
        self.writes, self.reads, self.visited = [], [], set()
        self.instruction_count = self.extension_count = 0
        self.error = None
        self.pending_extension = None
        try:
            cursor = address
            while True:
                self.u.emu_start(cursor, end, count=100001)
                if not self.pending_extension:
                    break
                word, pc, size = self.pending_extension
                self.pending_extension = None
                cursor = self.execute_extension(word, pc)
                if size == 2:
                    assert cursor == pc + 4, "EXEC.IT control-flow needs explicit semantics"
                    cursor = pc + 2
                self.u.reg_write(PC, cursor & 0xffffffff)
                self.extension_count += 1
        except un.UcError as e:
            raise AssertionError(self.error or (str(e) + " at " + hex(self.u.reg_read(PC)))) from e
        assert not self.error, self.error
        assert self.u.reg_read(PC) == end, (hex(address), hex(self.u.reg_read(PC)))
        if end == STOP:
            for i in [2, *CALLEE]:
                assert self.reg(i) == before[i], ("ABI register", i, hex(before[i]), hex(self.reg(i)))
            # A leaf entry may tail-call the displaced prologue; outer RA must survive.
            assert self.reg(1) == before[1]
        return self


def expected_bar(value):
    p = min(value, 100)
    red = 162 if p <= 50 else 162 * (100 - p) // 50
    green = 162 if p >= 50 else 162 * p // 50
    frame = []
    for i in range(9):
        coverage = max(0, min(100, 9 * p - 100 * i))
        frame.extend([red * coverage // 100, green * coverage // 100, 0])
    return bytes(frame)


def check_image(stock, modified, manifest):
    assert h(stock) == manifest["stock_sha256"]
    assert h(modified) == manifest["modified_sha256"]
    assert stock[:0x18] == modified[:0x18]
    for image in [stock, modified]:
        assert (len(image) - 4) % 16 == 0
        assert struct.unpack_from("<I", image, 0x18)[0] == len(image)
        assert image[0x20:0x24] == b"KNLT"
        assert struct.unpack("<I", image[-4:])[0] == (zlib.crc32(image[:-4]) ^ 0xffffffff)
    allow = {i for p in manifest["patched_ranges"]
             for i in range(int(p["offset"], 16), int(p["offset"], 16) + p["length"])}
    changes = {i for i, (a, b) in enumerate(zip(stock[:-4], modified)) if a != b}
    assert changes <= allow
    assert len(modified) < 128 * 1024
    assert stock[0x190:0x91b4] == modified[0x190:0x91b4], "All relocated RAM machine code preserved"
    assert stock[0x1c000:0x1ed50] == modified[0x1c000:0x1ed50], "Tables and initialized data preserved"
    assert modified[0x1c9dc:0x1c9e5] == bytes(range(84, 93))
    return {"status": "PASS", "byte_changes_in_original_body": len(changes),
            "allowed_ranges": manifest["patched_ranges"], "radio_and_other_original_code": "byte-identical outside listed display/startup/header patches",
            "all_ram_code_and_initialized_data_preserved": True,
            "image_header_crc_alignment_and_128k_limit": "PASS"}


def check_startup(stock, modified):
    # Execute the stock BSS-end AUIPC/ADDI and word-zero loop, not a Python copy.
    a, b = FirmwareEmulator(stock), FirmwareEmulator(modified)
    for e in [a, b]:
        e.u.mem_write(0x809b0, b"\xa5" * (0x826a4 - 0x809b0))
        # Inspect actual startup for the setup PC and stop just after BSS loop.
        e.call(XIP + 0x132, end=XIP + 0x152)
    assert bytes(a.u.mem_read(0x809b0, 0x82698 - 0x809b0)) == b"\0" * (0x82698 - 0x809b0)
    assert bytes(b.u.mem_read(0x809b0, 0x8269c - 0x809b0)) == b"\0" * (0x8269c - 0x809b0)
    assert a.word(TICK) == 0xa5a5a5a5 and b.word(TICK) == 0
    assert b.word(0x8269c) == 0xa5a5a5a5, "No clearing beyond new BSS end"
    return {"status": "PASS", "old_end": "0x82698", "new_end": "0x8269c",
            "new_state_zeroed_by_actual_startup": True, "adjacent_guard_unchanged": True}


def query(e, press, now):
    e.reset_registers()
    e.u.reg_write(REG[10], 0x5f88)
    e.byte(0x80a6e, press)
    e.clock(now)
    e.call(XIP + 0x1313c)


def check_query(stock, modified, symbols):
    # Exercise the entire stock custom-key dispatch through the actual Fn+B hooks.
    a, b = FirmwareEmulator(stock), FirmwareEmulator(modified)
    for flags in range(256):
        for e in [a, b]:
            e.byte(FLAGS, flags)
            e.byte(0x80aa2, 75)
            e.byte(0x80b45, 37)
        now = 0x12345678 + flags
        query(a, 1, now); query(b, 1, now)
        assert b.byte(FLAGS) == a.byte(FLAGS) == ((flags & 0xfe) | 2)
        assert b.byte(0x80aa2) == b.byte(0x80b45) == 0
        assert b.word(TICK) == now
        assert symbols["patch_begin"] in b.visited
        assert XIP + 0x135f4 in b.visited
        assert XIP + 0x13528 not in b.visited
        # Release goes directly to the original epilogue and keeps state untouched.
        b.byte(0x80a6e, 0)
        before = bytes(b.u.mem_read(0x80000, 0x269c))
        query(b, 0, now + 1)
        assert bytes(b.u.mem_read(0x80000, 0x269c)) == before
        assert symbols["patch_begin"] not in b.visited
    return {"status": "PASS", "all_256_flag_values": True, "press_and_release_paths": True,
            "fn_b_stock_pressed_guard_and_epilogue": True, "timer_and_old_counter_init": True}


def check_bar(modified):
    e = FirmwareEmulator(modified)
    seed = random.Random(0x514d3830)
    original = bytes(seed.randrange(256) for _ in range(279))
    allowed = {OUTPUT, *range(SIDE, SIDE + 27)}
    frames = {}
    combinations = 0
    max_instructions = 0
    for p in range(256):
        for mode in range(3):
            for charge in range(3):
                e.reset_registers()
                e.u.mem_write(FRAME, original)
                e.byte(FRAME - 1, 0x6d); e.byte(FRAME + 279, 0xe7)
                e.byte(PERCENT, p); e.byte(MODE, mode); e.byte(0x80b44, charge)
                e.byte(FLAGS, 0xf2); e.byte(OUTPUT, 0xff)
                e.word(TICK, 0x10203040); e.clock(0x10203040 + VIEW - 1)
                e.call(XIP + 0x98f0)
                assert bytes(e.u.mem_read(SIDE, 27)) == expected_bar(p), (p, mode, charge)
                assert bytes(e.u.mem_read(FRAME, 84 * 3)) == original[:84 * 3]
                assert e.byte(FRAME - 1) == 0x6d and e.byte(FRAME + 279) == 0xe7
                assert e.byte(FLAGS) == 0xf2 and e.byte(OUTPUT) == 0xfd
                assert e.byte(PERCENT) == p and e.byte(MODE) == mode and e.byte(0x80b44) == charge
                for addr, size, _ in e.writes:
                    assert set(range(addr, addr + size)) <= allowed, (hex(addr), size)
                assert all(addr != XIP + 0x98f4 for addr in e.visited)
                assert not any(addr >= 0x80000000 for addr, _, _ in e.writes), "No MMIO writes"
                max_instructions = max(max_instructions, e.instruction_count)
                combinations += 1
        if p in [0, 10, 25, 50, 75, 88, 100, 255]:
            frames[str(p)] = [list(expected_bar(p)[i:i + 3]) for i in range(0, 27, 3)]
    # Timer subtraction must work over the 32-bit counter wrap and repeated Fn+B.
    for start in [0, 0xffffffff - VIEW // 2, 0xfffffff0, 0x12345678]:
        e.word(TICK, start); e.clock(start + 1000); e.byte(FLAGS, 2); e.byte(PERCENT, 88)
        e.reset_registers(); e.call(XIP + 0x98f0)
        assert bytes(e.u.mem_read(SIDE, 27)) == expected_bar(88)
    query(e, 1, 0x20000000)
    query(e, 1, 0x20000000 + VIEW - 10)
    e.clock(0x20000000 + VIEW + 20); e.reset_registers(); e.call(XIP + 0x98f0)
    assert e.byte(FLAGS) & 2 and bytes(e.u.mem_read(SIDE, 27)) == expected_bar(88)
    return {"status": "PASS", "percent_mode_charge_combinations": combinations,
            "input_bytes_0_to_255_clamped_to_100": True, "main_leds_and_guards_unchanged": True,
            "no_mmio_writes_or_radio_calls": True, "wrap_and_retrigger": True,
            "callee_saved_gp_tp_sp_ra_preserved": True,
            "maximum_rv32_instructions_per_active_render": max_instructions, "sample_rgb_frames": frames}


def check_original_render(stock, modified):
    a, b = FirmwareEmulator(stock), FirmwareEmulator(modified)
    rng = random.Random(0x4d513830)
    tested = 0
    for side_mode in range(5):
        for charge in range(3):
            for led_off in [False, True]:
                for elapsed in [0, 14, 50, 64]:
                    for e in [a, b]:
                        e.reset_registers()
                    ram = bytearray(0x269c)
                    ram[:0x9b0] = stock[0x1e3a0:0x1ed50]
                    ram[FRAME - 0x80000:FRAME - 0x80000 + 279] = bytes(rng.randrange(256) for _ in range(279))
                    ram[0x8ca] = side_mode
                    ram[0x8cf] = 1
                    ram[0x8d0] = 3
                    ram[0xb44] = charge
                    ram[0xb57] = 8 if led_off else 0
                    ram[0xb58] = 0 # Battery query is inactive.
                    ram[0xb8e] = elapsed
                    for e in [a, b]:
                        e.u.mem_write(0x80000, bytes(ram))
                        e.call(XIP + 0x98f0)
                    assert bytes(a.u.mem_read(0x80000, 0x269c)) == bytes(b.u.mem_read(0x80000, 0x269c)), (side_mode, charge, led_off, elapsed)
                    assert XIP + 0x98f4 in b.visited
                    # The displaced prologue and stack writes remain exactly identical.
                    assert bytes(a.u.mem_read(RAM_END - 16, 16)) == bytes(b.u.mem_read(RAM_END - 16, 16))
                    tested += 1
    return {"status": "PASS", "inactive_original_render_cases": tested,
            "all_five_side_modes_charge_and_off_states": True, "trampoline_and_stock_stack_frame_equivalent": True}


def check_expiry(stock, modified):
    # v2 samples expiry in the enclosing stock frame task, once per frame.
    tested = 0
    for side_mode in range(5):
        for charge in range(3):
            for when in [VIEW, VIEW + 1, VIEW + 16000000]:
                for side_enabled in [0, 1]:
                    a, b = FirmwareEmulator(stock), FirmwareEmulator(modified)
                    for e in [a, b]:
                        fields = {0x808ca: side_mode, 0x808cf: 1, 0x808d0: 3,
                                  0x80b44: charge, OUTPUT: 2,
                                  FLAGS: 0xf2 if e is b else 0xf0,
                                  0x80aa2: 0, 0x80b45: 0,
                                  0x80b8e: 17 if e is b else 64,
                                  0x80be3: 10, 0x80993: side_enabled,
                                  0x808b1: 0, 0x808b2: 1, 0x808cc: 1,
                                  0x808cd: 3, 0x80ba1: 20, 0x80ba3: 60}
                        for address, value in fields.items(): e.byte(address, value)
                        e.word(TICK, 0xffff0000)
                        e.clock(0xffff0000 + when)
                        e.u.mem_write(SIDE, expected_bar(88) if e is b else bytes(27))
                        e.call(XIP + 0xc400)
                    assert bytes(a.u.mem_read(0x80000, 0x269c)) == bytes(b.u.mem_read(0x80000, 0x269c)), (side_mode, charge, when, side_enabled)
                    assert b.byte(FLAGS) == 0xf0
                    tested += 1
    return {"status": "PASS", "expiry_cases": tested, "exact_3s_boundary_and_wrap": True,
            "query_flag_only_cleared": True, "stock_frame_and_off_state_restored": True}


def check_encoded_output(modified):
    # Execute the stock RGB encoder on actual generated frames, verify that it
    # transforms all 93 triplets to 24-byte RGB waveforms and keeps outer guards.
    e = FirmwareEmulator(modified)
    tested = 0
    for p in [0, 10, 25, 50, 75, 88, 100, 255]:
        e.reset_registers()
        e.u.mem_write(FRAME, bytes(279))
        e.byte(PERCENT, p); e.byte(FLAGS, 2)
        e.word(TICK, 100); e.clock(200)
        e.call(XIP + 0x98f0)
        # The byte before the encoded buffer belongs to the RGB frame itself.
        # Do not mutate the generated frame to create a guard.
        before_rgb = bytes(e.u.mem_read(FRAME, 279))
        e.byte(0x82694, 0xa5)
        e.reset_registers(); e.call(XIP + 0x15f58)
        encoded = bytes(e.u.mem_read(0x81ddc, 93 * 24))
        assert set(encoded) <= {0xc0, 0xf0}, set(encoded)
        assert bytes(e.u.mem_read(FRAME, 279)) == before_rgb and e.byte(0x82694) == 0xa5
        # Encoder emits G,R,B, MSB first, as proven by the original instruction path.
        rgb = bytes(e.u.mem_read(FRAME, 279))
        expected = bytes(0xf0 if (channel & (1 << bit)) else 0xc0
                         for i in range(93) for channel in [rgb[i * 3 + 1], rgb[i * 3], rgb[i * 3 + 2]]
                         for bit in range(7, -1, -1))
        assert encoded == expected, (p, encoded[-24:].hex(), expected[-24:].hex())
        tested += 1
    return {"status": "PASS", "sample_frames_encoded_by_stock_machine_code": tested,
            "waveform_bytes_and_grb_order": True, "encoded_buffer_guards_unchanged": True}


def check_total_frame(stock, modified):
    a, b = FirmwareEmulator(stock), FirmwareEmulator(modified)
    rng = random.Random(0x514d4652)
    scenarios = [
        ("normal", {}),
        ("side_disabled", {0x80993: 0}),
        ("connection_indicator_1", {0x809f0: 1}),
        ("connection_indicator_2", {0x809f0: 2}),
        ("connection_indicator_3", {0x809f0: 3}),
        ("reset_indicator", {0x80bba: 1}),
        ("low_battery_indicator", {0x80b90: 1}),
        ("primary_frame_enabled", {0x808cc: 0, 0x808b1: 0}),
        ("lighting_disabled", {0x808b2: 0, 0x808cd: 0, 0x80993: 0}),
        ("stock_tick_gate", {0x80be3: 7}),
    ]
    counts = {"active": 0, "inactive": 0, "expired": 0}
    for side_mode in range(5):
        for mode in range(3):
            for charge in range(3):
                for name, extra in scenarios:
                    for state in counts:
                        ram = bytearray(0x269c)
                        ram[:0x9b0] = stock[0x1e3a0:0x1ed50]
                        ram[FRAME - 0x80000:FRAME - 0x80000 + 279] = bytes(rng.randrange(256) for _ in range(279))
                        fields = {PERCENT: 88, FLAGS: 0, OUTPUT: 2, MODE: mode,
                                  0x80b44: charge, 0x80be3: 10, 0x809f0: 0,
                                  0x80bba: 0, 0x80b90: 0, 0x80993: 1,
                                  0x808b1: 0, 0x808b2: 1, 0x808cc: 1,
                                  0x808ca: side_mode, 0x808cd: 3,
                                  0x808cf: 1, 0x808d0: 3,
                                  0x80ba1: 20, 0x80ba3: 60, 0x80b45: 10,
                                  0x80b79: 5, 0x80b8e: 64, **extra}
                        for address, value in fields.items(): ram[address - 0x80000] = value
                        if state == "expired":
                            ram[SIDE - 0x80000:SIDE - 0x80000 + 27] = bytes(27)
                            ram[0x80aa2 - 0x80000] = ram[0x80b45 - 0x80000] = 0
                        for e in [a, b]:
                            e.reset_registers()
                            e.u.mem_write(0x80000, bytes(ram))
                            e.word(TICK, 0xffff0000)
                            e.clock(0xffff0000 + (VIEW if state == "expired" else 100))
                        if state != "inactive": b.byte(FLAGS, 2)
                        if state == "expired": b.u.mem_write(SIDE, expected_bar(88))
                        a.call(XIP + 0xc400); b.call(XIP + 0xc400)
                        assert bytes(a.u.mem_read(FRAME, 252)) == bytes(b.u.mem_read(FRAME, 252)), ("main frame", state, name, side_mode, mode, charge)
                        assert a.byte(MODE) == b.byte(MODE) == mode
                        if state == "active":
                            assert bytes(b.u.mem_read(SIDE, 27)) == expected_bar(88), ("bar priority", name, side_mode, mode, charge)
                            assert b.byte(FLAGS) & 2 and not (b.byte(OUTPUT) & 2)
                        else:
                            assert bytes(a.u.mem_read(0x80000, 0x269c)) == bytes(b.u.mem_read(0x80000, 0x269c)), ("stock frame equivalence", state, name, side_mode, mode, charge)
                            assert not (b.byte(FLAGS) & 2)
                        counts[state] += 1
    return {"status": "PASS", "case_counts": counts, "scenarios": [n for n, _ in scenarios],
            "all_five_side_modes_three_connections_three_charge_states": True,
            "primary_84_rgb_triplets_match_stock": True,
            "inactive_and_expired_entire_data_ram_match_stock": True,
            "active_bar_has_priority_over_stock_indicators_and_off_gates": True,
            "added_stack_depth_bytes": 16, "abi_preserved": True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    m = json.loads((args.build / "build-manifest.json").read_text())
    stock = (args.build / "MQ80-CS836B-V0109-20241220-STOCK.bin").read_bytes()
    modified = (args.build / m["modified_name"]).read_bytes()
    symbols = {k: int(v, 16) for k, v in m["symbols"].items()}
    checks = {}
    tests = [
        ("image_structure_and_binary_preservation", lambda: check_image(stock, modified, m)),
        ("startup_bss", lambda: check_startup(stock, modified)),
        ("fn_b_dispatch", lambda: check_query(stock, modified, symbols)),
        ("battery_bar_machine_code", lambda: check_bar(modified)),
        ("inactive_stock_renderer", lambda: check_original_render(stock, modified)),
        ("expiry_and_restore", lambda: check_expiry(stock, modified)),
        ("complete_stock_frame_task", lambda: check_total_frame(stock, modified)),
        ("stock_rgb_encoder", lambda: check_encoded_output(modified)),
    ]
    for name, test in tests:
        print("Checking", name, flush=True)
        checks[name] = test()
        print(name, "PASS", flush=True)
    result = {
        "status": "PASS_OFFLINE_ONLY_NOT_FLASHED", "modified_sha256": h(modified),
        "emulator": "Unicorn " + un.__version__ + " RV32 with stock Andes instruction adapter",
        "scope": "Actual Fn+B dispatcher, display hooks, compiled patch, complete stock RGB frame task, stock side renderer, stock RGB encoder and BSS zero loop; mocked RAM and system timer",
        "not_tested": ["Hardware boot/OTA acceptance", "2.4G/Bluetooth radio execution or pairing", "Physical RGB LED direction/channel appearance", "MCU battery ADC accuracy", "Real task/interrupt concurrency and timing"],
        "checks": checks,
    }
    args.out.write_text(json.dumps(result, indent=2) + "\n")
    m["status"] = result["status"]
    m["validation_file"] = args.out.name
    (args.build / "build-manifest.json").write_text(json.dumps(m, indent=2) + "\n")
    print("All offline checks passed", flush=True)


if __name__ == "__main__":
    main()
