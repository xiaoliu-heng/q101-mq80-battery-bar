"""Build the experimental side-light HID image offline; never opens hardware."""
import argparse
import hashlib
import json
import re
import struct
import subprocess
import zlib
from pathlib import Path

STOCK_SHA = 'd19733978808c856db93236ea3472b2cf843493c9aa6d5320db50412431e5210'
XIP = 0x20000000
APPEND = 0x1ed50
BSS_END = 0x826d0
ORIGINAL = {0x142:'130eae55', 0x98f0:'411122c6', 0xc400:'011122cc',
            0x13520:'8b078035f99b93e7', 0xe22c:'8b14803e',
            0xdfc4:'03876781', 0xe1d8:'03876781',
            0x112ac:'411126c2', 0x12a4e:'93058002'}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def jal(pc, target, rd=0):
    delta = target-pc
    assert delta % 2 == 0 and -(1<<20) <= delta < 1<<20
    n = delta & 0x1fffff
    return struct.pack('<I',0x6f | rd<<7 | (n>>20&1)<<31 | (n>>1&1023)<<21
                       | (n>>11&1)<<20 | (n>>12&255)<<12)


def build(stock_path, toolchain, out):
    src = Path(__file__).resolve().parent
    out.mkdir(parents=True,exist_ok=True)
    version = json.loads((src/'version.json').read_text())
    assert re.fullmatch(r'(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)', version['version']), 'Requires numeric major.minor.patch'
    parts = list(map(int, version['version'].split('.')))
    assert all(0 <= n <= 255 for n in parts)
    assert type(version['build']) is int and 1 <= version['build'] <= 65535
    assert type(version['feature_flags']) is int and 0 <= version['feature_flags'] <= 255
    firmware_name = 'MQ80-CS836B-V0109-20241220-BatteryBar-SideHID-v%s-USB-RF-EXPERIMENTAL.bin' % version['version']
    header = '\n'.join('#define SIDE_FW_%s %du' % (key, value) for key, value in
                       zip(['MAJOR','MINOR','PATCH','BUILD','FEATURE_FLAGS'],
                           [*parts,version['build'],version['feature_flags']]))+'\n'
    (out/'firmware_version.h').write_text(header)
    stock = stock_path.read_bytes()
    assert digest(stock) == STOCK_SHA, 'Requires the exact MQ80 V0109 stock image'
    assert len(stock) == APPEND+4
    assert stock[0x20:0x24] == b'KNLT'
    assert struct.unpack_from('<I',stock,0x18)[0] == len(stock)
    assert int.from_bytes(stock[-4:],'little') == zlib.crc32(stock[:-4]) ^ 0xffffffff
    for offset,expected in ORIGINAL.items():
        assert stock[offset:offset+len(bytes.fromhex(expected))] == bytes.fromhex(expected), hex(offset)

    def run(name,*args):
        return subprocess.run([str(toolchain.resolve()/('riscv-none-elf-'+name)),
                               *map(str,args)],check=True,capture_output=True,text=True).stdout
    arch = ['-march=rv32im','-mabi=ilp32','-mno-relax']
    flags = [*arch,'-Os','-msmall-data-limit=0','-ffreestanding','-fno-builtin','-fno-pic',
             '-fno-stack-protector','-fno-unwind-tables','-fno-asynchronous-unwind-tables',
             '-ffunction-sections','-fdata-sections','-Wall','-Wextra','-Werror']
    run('gcc',*flags,'-I',out.resolve(),'-c',src/'side_hid.c','-o',out/'side_hid.o')
    run('gcc',*arch,'-c',src/'hid_trampoline.S','-o',out/'hid_trampoline.o')
    run('gcc',*arch,'-c',src.parent/'source/trampoline.S','-o',out/'battery_trampoline.o')
    elf = out/'side_hid.elf'
    run('gcc',*arch,'-nostdlib','-nostartfiles','-Wl,--no-relax,--build-id=none',
        '-Wl,-T,'+str(src/'patch.ld'),'-Wl,-Map,'+str(out/'side_hid.map'),
        out/'side_hid.o',out/'hid_trampoline.o',out/'battery_trampoline.o','-o',elf)
    assert not run('nm','-u',elf).strip(), 'Unexpected unresolved symbols'
    symbols = {p[2]:int(p[0],16) for line in run('nm','-n',elf).splitlines()
               if len(p:=line.split()) == 3}
    run('objcopy','-O','binary',elf,out/'side_hid.machine.bin')
    code = (out/'side_hid.machine.bin').read_bytes()
    (out/'side_hid.disassembly.txt').write_text(run('objdump','-d','-M','no-aliases',elf))
    (out/'side_hid.relocations.txt').write_text(run('objdump','-r',elf))
    body = bytearray(stock[:-4])
    patches = []

    def patch(offset,data,reason):
        patches.append({'offset':hex(offset),'length':len(data),'reason':reason,
                        'original':stock[offset:offset+len(data)].hex(),'modified':data.hex()})
        body[offset:offset+len(data)] = data
    old = int.from_bytes(stock[0x142:0x146],'little')
    new = (old & 0xfffff) | (((old>>20)+(BSS_END-0x82698))<<20)
    patch(0x142,struct.pack('<I',new),'Reserve battery timer + 52-byte host/effect state in BSS')
    for offset,name,reason in [
        (0x98f0,'patch_side_entry','Existing Fn+B side renderer'),
        (0xc400,'patch_frame_entry','Host lease, battery priority and final side overlay'),
        (0xe22c,'patch_hid_dispatch_entry','Shared USB/RF request dispatch'),
        (0xdfc4,'patch_usb_length_general','Record actual report length in general USB receiver'),
        (0xe1d8,'patch_usb_length_direct','Record actual report length in direct VIA receiver'),
        (0x112ac,'patch_rf_fast_sleep','Cancel local notification before original RF fast sleep'),
        (0x12a4e,'patch_rf_normal_sleep','Cancel local notification before original RF normal sleep')]:
        patch(offset,jal(XIP+offset,symbols[name]),reason)
    patch(0x13520,jal(XIP+0x13520,symbols['patch_begin'],1)+jal(XIP+0x13524,XIP+0x135f4),
          'Existing Fn+B press/retrigger hook')
    body.extend(code)
    body.extend(b'\xff'*((-len(body))%16))
    patch(0x18,struct.pack('<I',len(body)+4),'OTA image length')
    modified = bytes(body)+struct.pack('<I',zlib.crc32(body)^0xffffffff)
    assert len(modified) < 0x20000
    allowed = {i for p in patches for i in range(int(p['offset'],16),int(p['offset'],16)+p['length'])}
    actual = {i for i,(a,b) in enumerate(zip(stock[:-4],modified)) if a != b}
    assert actual <= allowed
    assert stock[0x190:0x91b4] == modified[0x190:0x91b4]
    assert stock[0x1c000:APPEND] == modified[0x1c000:APPEND]
    (out/firmware_name).write_bytes(modified)
    (out/'stock.bin').write_bytes(stock)
    manifest = {'status':'BUILT_OFFLINE_NOT_FLASHED','base_firmware':'MQ80-CS836B-V0109-20241220',
                'modified_name':firmware_name,'modified_size':len(modified),'modified_sha256':digest(modified),
                'modified_crc32_telink':'%08X'%(zlib.crc32(body)^0xffffffff),
                'stock_sha256':digest(stock),'compiled_code_size':len(code),
                'actual_original_byte_changes':len(actual),'patched_ranges':patches,
                'symbols':{k:hex(v) for k,v in symbols.items()},'bss_end':hex(BSS_END),
                'compiler':run('gcc','--version').splitlines()[0],'compiler_flags':flags,
                'control_transports':['usb','rf'],'rf_info_query':True,
                'firmware_version':version['version'],'firmware_build':version['build'],
                'firmware_feature_flags':version['feature_flags'],'version_metadata_format':1,
                'rf_sleep_policy':'PLAY never resets idle counters; original RF sleep cancels the notification. Legacy FRAME keeps its original bounded-lease behavior.',
                'local_effects':['solid','blink','breathe','rainbow'],
                'local_effect_duration_ms':[100,60000],
                'device_access':'NONE'}
    (out/'build-manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stock',type=Path,required=True)
    parser.add_argument('--toolchain',type=Path,required=True)
    parser.add_argument('--out',type=Path,required=True)
    args = parser.parse_args()
    m = build(args.stock,args.toolchain,args.out)
    print(json.dumps({k:m[k] for k in ['modified_name','modified_size','compiled_code_size',
                                     'modified_sha256','modified_crc32_telink']}))
