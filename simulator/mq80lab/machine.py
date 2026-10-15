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

