"""Compile and inject locally. This script has no device or flashing functions.

Usage: python3 build_patch.py --stock ORIGINAL.bin --toolchain PATH_TO_BIN --out BUILD
Requires a RISC-V bare-metal GCC toolchain; ELF is linked for RV32IM/ILP32.
"""
import argparse
import hashlib
import json
import struct
import subprocess
import zlib
from pathlib import Path

STOCK_SHA256 = "d19733978808c856db93236ea3472b2cf843493c9aa6d5320db50412431e5210"
XIP = 0x20000000
APPEND_OFFSET = 0x1ed50
PATCHES = {
    0x142: bytes.fromhex("130eae55"),
    0x98f0: bytes.fromhex("411122c6"),
    0xc400: bytes.fromhex("011122cc"),
    0x13520: bytes.fromhex("8b078035f99b93e7"),
}
MODIFIED_NAME = "MQ80-CS836B-V0109-20241220-FnB-BatteryBar-v2-EXPERIMENTAL.bin"


def sha(data):
    return hashlib.sha256(data).hexdigest()


def crc(data):
    return zlib.crc32(data) ^ 0xffffffff


def jal(pc, target, rd):
    d = target - pc
    assert d % 2 == 0 and -(1 << 20) <= d < (1 << 20), (pc, target)
    u = d & 0x1fffff
    word = (0x6f | (rd << 7) | (((u >> 20) & 1) << 31)
            | (((u >> 1) & 0x3ff) << 21) | (((u >> 11) & 1) << 20)
            | (((u >> 12) & 0xff) << 12))
    return struct.pack("<I", word)


def main():
    a = argparse.ArgumentParser(description=__doc__)
    a.add_argument("--stock", type=Path, required=True)
    a.add_argument("--toolchain", type=Path, required=True)
    a.add_argument("--out", type=Path, required=True)
    args = a.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    src = Path(__file__).resolve().parent
    stock = args.stock.read_bytes()
    assert sha(stock) == STOCK_SHA256, "This patch accepts only the exact analyzed stock image"
    assert len(stock) == APPEND_OFFSET + 4
    assert struct.unpack_from("<I", stock, 0x18)[0] == len(stock)
    assert stock[0x20:0x24] == b"KNLT"
    assert struct.unpack("<I", stock[-4:])[0] == crc(stock[:-4])
    for off, old in PATCHES.items():
        assert stock[off:off + len(old)] == old, hex(off)

    def run(tool, *argv):
        cmd = [str(args.toolchain.resolve() / ("riscv-none-elf-" + tool)), *map(str, argv)]
        r = subprocess.run(cmd, check=True, capture_output=True, text=True)
        return r.stdout

    flags = ["-march=rv32im", "-mabi=ilp32", "-Os", "-msmall-data-limit=0",
             "-mno-relax", "-ffreestanding", "-fno-builtin", "-fno-pic",
             "-fno-stack-protector", "-fno-unwind-tables", "-fno-asynchronous-unwind-tables",
             "-ffunction-sections", "-fdata-sections", "-Wall", "-Wextra", "-Werror"]
    elf = args.out / "battery_bar.elf"
    machine = args.out / "battery_bar.machine.bin"
    run("gcc", *flags, "-c", src / "battery_bar.c", "-o", args.out / "battery_bar.o")
    run("gcc", "-march=rv32im", "-mabi=ilp32", "-mno-relax", "-c",
        src / "trampoline.S", "-o", args.out / "trampoline.o")
    run("gcc", "-march=rv32im", "-mabi=ilp32", "-mno-relax", "-nostdlib", "-nostartfiles",
        "-Wl,--no-relax,--build-id=none", "-Wl,-T," + str(src / "patch.ld"),
        "-Wl,-Map," + str(args.out / "battery_bar.map"),
        args.out / "battery_bar.o", args.out / "trampoline.o", "-o", elf)
    assert run("nm", "-u", elf).strip() == "", "Unexpected unresolved symbols"
    nm = run("nm", "-n", elf)
    symbols = {}
    for line in nm.splitlines():
        parts = line.split()
        if len(parts) == 3:
            symbols[parts[2]] = int(parts[0], 16)
    run("objcopy", "-O", "binary", elf, machine)
    code = machine.read_bytes()
    (args.out / "battery_bar.disassembly.txt").write_text(run("objdump", "-d", "-M", "no-aliases", elf))
    (args.out / "battery_bar.relocations.txt").write_text(run("objdump", "-r", elf))
    assert symbols["patch_start_tick"] == 0x82698
    assert symbols["patch_side_entry"] == XIP + APPEND_OFFSET
    assert symbols["stock_side_resume"] == XIP + 0x98f4
    trampoline_offset = symbols["patch_stock_side"] - XIP - APPEND_OFFSET
    assert code[trampoline_offset:trampoline_offset + 4] == PATCHES[0x98f0]
    frame_trampoline_offset = symbols["patch_stock_frame"] - XIP - APPEND_OFFSET
    assert code[frame_trampoline_offset:frame_trampoline_offset + 4] == PATCHES[0xc400]

    body = bytearray(stock[:-4])
    # Only immediate of the BSS end changes: 0x82698 -> 0x8269c.
    body[0x142:0x146] = bytes.fromhex("130eee55")
    body[0x98f0:0x98f4] = jal(XIP + 0x98f0, symbols["patch_side_entry"], 0)
    body[0xc400:0xc404] = jal(XIP + 0xc400, symbols["patch_frame_entry"], 0)
    body[0x13520:0x13524] = jal(XIP + 0x13520, symbols["patch_begin"], 1)
    body[0x13524:0x13528] = jal(XIP + 0x13524, XIP + 0x135f4, 0)
    body.extend(code)
    body.extend(b"\xff" * ((-len(body)) % 16))
    struct.pack_into("<I", body, 0x18, len(body) + 4)
    checksum = crc(body)
    modified = bytes(body) + struct.pack("<I", checksum)
    assert len(modified) < 0x20000
    (args.out / MODIFIED_NAME).write_bytes(modified)
    (args.out / "MQ80-CS836B-V0109-20241220-STOCK.bin").write_bytes(stock)
    patches = []
    for off, length, why in [(0x18, 4, "OTA header image size"),
                             (0x142, 4, "Reserve 4 B at old BSS end"),
                             (0x98f0, 4, "Side renderer entry jump"),
                             (0xc400, 4, "Total-frame task entry jump for final battery overlay"),
                             (0x13520, 8, "Fn+B press helper and return to stock epilogue")]:
        patches.append({"offset": hex(off), "length": length, "reason": why,
                        "original": stock[off:off + length].hex(),
                        "modified": modified[off:off + length].hex()})
    allow = {i for p in patches for i in range(int(p["offset"], 16), int(p["offset"], 16) + p["length"])}
    actual = {i for i, (x, y) in enumerate(zip(stock[:-4], modified)) if x != y}
    assert actual <= allow
    manifest = {
        "status": "BUILT_NOT_FLASHED_SEE_VALIDATION_JSON",
        "base_firmware": "MQ80-CS836B-V0109-20241220", "stock_sha256": sha(stock),
        "modified_name": MODIFIED_NAME, "modified_sha256": sha(modified),
        "stock_size": len(stock), "modified_size": len(modified),
        "stock_crc32_telink": hex(crc(stock[:-4])), "modified_crc32_telink": hex(checksum),
        "append_offset": hex(APPEND_OFFSET), "compiled_code_size": len(code),
        "appended_aligned_size": len(modified) - len(stock), "actual_original_byte_changes": len(actual),
        "patched_ranges": patches, "symbols": {k: hex(v) for k, v in symbols.items()},
        "compiler_version": run("gcc", "--version").splitlines()[0], "compiler_flags": flags,
        "raw_code_sha256": sha(code), "no_unresolved_symbols": True,
        "ram_start_tick": "0x82698", "bss_end_exclusive_original": "0x82698",
        "bss_end_exclusive_modified": "0x8269c", "display_ticks": 48000000,
        "radio_2_4g_code_preserved_byte_for_byte_except_display_hooks": True,
        "physical_validation": "NOT_PERFORMED", "device_access": "NONE"
    }
    (args.out / "build-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps({k: manifest[k] for k in ["modified_name", "modified_size", "compiled_code_size", "modified_sha256", "modified_crc32_telink"]}))


if __name__ == "__main__":
    main()
