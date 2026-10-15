"""Independent, small RV32 integer interpreter for the new leaf machine code.

Uses only Python standard library, without Unicorn or the Andes adapter. Executes
patch_begin and active patch_side_entry from the real image. This is a bounded
cross-check, not a full CPU/board implementation. Unsupported instructions fail.
"""
import argparse
import hashlib
import json
import random
from fractions import Fraction
from pathlib import Path

XIP, STOP = 0x20000000, 0x40000000
BASE, END = 0x80000, 0x8269c
PERCENT, FLAGS, OUTPUT, TICK = 0x80948, 0x80b58, 0x80b57, 0x82698
SIDE, TIMER = 0x81dc0, 0x80140200
SAVED = [2, 3, 4, 8, 9, *range(18, 28)]


def signed(value, width=32):
    value &= (1 << width) - 1
    return value - (1 << width) if value & (1 << (width - 1)) else value


class Runner:
    def __init__(self, image, seed):
        self.image = image
        self.ram = bytearray(END - BASE)
        rng = random.Random(seed)
        self.regs = [rng.getrandbits(32) for _ in range(32)]
        self.regs[0], self.regs[1], self.regs[2], self.regs[3] = 0, STOP, 0xa0000, 0x80800
        self.pc = 0
        self.now = 0
        self.allowed = set()
        self.writes = []

    def load(self, address, size):
        if XIP <= address and address + size <= XIP + len(self.image):
            return int.from_bytes(self.image[address-XIP:address-XIP+size], 'little')
        if BASE <= address and address + size <= END:
            return int.from_bytes(self.ram[address-BASE:address-BASE+size], 'little')
        if address == TIMER and size == 4:
            return self.now & 0xffffffff
        raise AssertionError(('unmapped read', hex(address), size))

    def store(self, address, size, value):
        assert set(range(address, address + size)) <= self.allowed, ('unexpected store', hex(address), size)
        assert BASE <= address and address + size <= END
        value &= (1 << (8 * size)) - 1
        self.ram[address-BASE:address-BASE+size] = value.to_bytes(size, 'little')
        self.writes.append((address, size))

    def set_byte(self, address, value):
        self.ram[address - BASE] = value & 255

    def run(self, start):
        before = {i: self.regs[i] for i in [1, *SAVED]}
        self.pc = start
        steps = 0
        while self.pc != STOP:
            steps += 1
            assert steps < 2000, 'instruction budget exceeded'
            pc = self.pc
            w = self.load(pc, 4)
            assert w & 3 == 3, ('non-RV32I instruction in leaf code', hex(pc), hex(w))
            op, rd, f3, rs1, rs2, f7 = w & 127, (w >> 7) & 31, (w >> 12) & 7, (w >> 15) & 31, (w >> 20) & 31, w >> 25
            a, b, imm = self.regs[rs1], self.regs[rs2], signed(w >> 20, 12)
            value = None
            self.pc = (pc + 4) & 0xffffffff
            if op == 0x37:
                value = w & 0xfffff000
            elif op == 0x17:
                value = pc + (w & 0xfffff000)
            elif op == 0x13:
                if f3 == 0: value = a + imm
                elif f3 == 1: value = a << (rs2 & 31)
                elif f3 == 2: value = int(signed(a) < imm)
                elif f3 == 3: value = int(a < (imm & 0xffffffff))
                elif f3 == 4: value = a ^ imm
                elif f3 == 5: value = signed(a) >> rs2 if f7 == 0x20 else a >> rs2
                elif f3 == 6: value = a | imm
                elif f3 == 7: value = a & imm
            elif op == 0x33:
                if f7 == 1:
                    if f3 == 0: value = a * b
                    elif f3 == 5: value = a // b if b else 0xffffffff
                    else: raise AssertionError(('unimplemented M instruction', f3))
                elif f3 == 0: value = a - b if f7 == 0x20 else a + b
                elif f3 == 1: value = a << (b & 31)
                elif f3 == 2: value = int(signed(a) < signed(b))
                elif f3 == 3: value = int(a < b)
                elif f3 == 4: value = a ^ b
                elif f3 == 5: value = signed(a) >> (b & 31) if f7 == 0x20 else a >> (b & 31)
                elif f3 == 6: value = a | b
                elif f3 == 7: value = a & b
            elif op == 0x03:
                size = {0: 1, 1: 2, 2: 4, 4: 1, 5: 2}[f3]
                value = self.load((a + imm) & 0xffffffff, size)
                if f3 in [0, 1]: value = signed(value, size * 8)
            elif op == 0x23:
                offset = signed(((w >> 25) << 5) | ((w >> 7) & 31), 12)
                self.store((a + offset) & 0xffffffff, {0: 1, 1: 2, 2: 4}[f3], b)
            elif op == 0x63:
                offset = signed(((w >> 31) << 12) | (((w >> 7) & 1) << 11) |
                                (((w >> 25) & 63) << 5) | (((w >> 8) & 15) << 1), 13)
                take = {0: a == b, 1: a != b, 4: signed(a) < signed(b),
                        5: signed(a) >= signed(b), 6: a < b, 7: a >= b}[f3]
                if take: self.pc = (pc + offset) & 0xffffffff
            elif op == 0x6f:
                offset = signed(((w >> 31) << 20) | (((w >> 12) & 255) << 12) |
                                (((w >> 20) & 1) << 11) | (((w >> 21) & 1023) << 1), 21)
                value, self.pc = pc + 4, (pc + offset) & 0xffffffff
            elif op == 0x67:
                assert f3 == 0
                value, self.pc = pc + 4, ((a + imm) & 0xffffffff) & ~1
            else:
                raise AssertionError(('unsupported opcode', hex(pc), hex(w)))
            if value is not None and rd:
                self.regs[rd] = value & 0xffffffff
            self.regs[0] = 0
        assert all(self.regs[i] == v for i, v in before.items()), 'ABI registers changed'
        return steps


def reference_bar(value):
    p = min(value, 100)
    red = 162 if p <= 50 else int(Fraction(162 * (100 - p), 50))
    green = 162 if p >= 50 else int(Fraction(162 * p, 50))
    length = Fraction(9 * p, 100)
    result = bytearray()
    for led in range(9):
        coverage = max(Fraction(0), min(Fraction(1), length - led))
        result.extend([int(red * coverage), int(green * coverage), 0])
    return bytes(result)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--build', type=Path, required=True)
    ap.add_argument('--out', type=Path, required=True)
    args = ap.parse_args()
    manifest = json.loads((args.build / 'build-manifest.json').read_text())
    image = (args.build / manifest['modified_name']).read_bytes()
    symbols = {k: int(v, 16) for k, v in manifest['symbols'].items()}
    max_steps = 0
    for flags in range(256):
        e = Runner(image, flags)
        e.set_byte(FLAGS, flags)
        e.set_byte(0x80aa2, 99)
        e.set_byte(0x80b45, 51)
        e.now = (0xfffffff0 + flags) & 0xffffffff
        e.allowed = {FLAGS, 0x80aa2, 0x80b45, *range(TICK, TICK + 4)}
        e.run(symbols['patch_begin'])
        assert e.load(FLAGS, 1) == (flags & 0xfe) | 2
        assert e.load(TICK, 4) == e.now
        assert e.load(0x80aa2, 1) == e.load(0x80b45, 1) == 0
    cases = 0
    for p in range(256):
        for index, flags in enumerate([2, 0x12, 0x72, 0xf2]):
            e = Runner(image, p * 4 + index)
            e.ram[:] = bytes((i * 37 + 23) % 256 for i in range(len(e.ram)))
            output = (p * 73 + index * 17) & 255
            e.set_byte(PERCENT, p)
            e.set_byte(FLAGS, flags)
            e.set_byte(OUTPUT, output)
            before = bytes(e.ram)
            e.allowed = {OUTPUT, *range(SIDE, SIDE + 27)}
            max_steps = max(max_steps, e.run(symbols['patch_side_entry']))
            assert e.load(OUTPUT, 1) == output & ~2
            assert e.load(FLAGS, 1) == flags
            assert e.ram[SIDE-BASE:SIDE-BASE+27] == reference_bar(p)
            changed = {BASE + i for i, (a, b) in enumerate(zip(before, e.ram)) if a != b}
            assert changed <= e.allowed
            cases += 1
    result = {'status': 'PASS_OFFLINE_ONLY', 'modified_sha256': hashlib.sha256(image).hexdigest(),
              'engine': 'separate Python standard-library RV32 integer interpreter; no Unicorn or Andes adapter',
              'fn_b_initialization_cases': 256, 'active_bar_cases': cases,
              'max_leaf_instructions': max_steps, 'exact_write_allowlists_and_ABI': True,
              'scope': 'new leaf functions only; not original firmware, full MCU, peripherals or concurrency'}
    args.out.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
