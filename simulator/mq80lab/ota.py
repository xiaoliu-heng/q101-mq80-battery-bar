"""Real OTA END / CRC on modeled NOR. Transport and bootloader are out of scope."""
import hashlib
import struct
from .engine import Runtime
from .profile import inspect_image


def run_ota(running,candidate,scenario='valid_image',active_bank=0):
    inspect_image(candidate)
    if scenario not in ['valid_image','incomplete','crc_error','header_readback_error']:
        raise ValueError('未知 OTA 场景。')
    if active_bank not in [0,0x40000]: raise ValueError('未知虚拟 Flash bank。')
    e=Runtime(running)
    target=0x40000-active_bank
    e.flash.data[:]=b'\xff'*0x100000
    e.flash.data[active_bank:active_bank+len(running)]=running
    original_active=bytes(e.flash.data[active_bank:active_bank+0x40000])
    e.flash.data[target:target+len(candidate)]=candidate
    e.flash.data[target:target+16]=b'\xff'*16
    e.flash.data[target+0x20]=0xff
    if scenario=='crc_error': e.flash.data[target+0x10000]^=0x80
    e.ota_readback_fault=scenario=='header_readback_error'
    e.ota_target=target
    e.word(0xb08,0x40000)
    e.word(0xb04,target)
    e.u.mem_write(0x810f8,candidate[:16])
    e.u.mem_write(0x81598,candidate[0x20:0x30])
    e.word(0x80a48,len(candidate))
    e.byte(0x80b52,1)
    e.byte(0x80a9c,0)
    e.u.mem_write(0x80b00,struct.pack('<H',(len(candidate)-4)//16-(scenario=='incomplete')))
    e.invoke('ota_end_crc',0x2000f828,registers={18:0x80a9c,2:0x9ff00},check_abi=False)
    success=scenario=='valid_image'
    unchanged=bytes(e.flash.data[active_bank:active_bank+0x40000])==original_active
    passed=(e.ota_result==0 and e.flash.data[target+0x20]==0x4b and e.flash.data[active_bank+0x20]==0) if success else (
        e.ota_result is not None and e.ota_result!=0 and unchanged and e.flash.data[target+0x20]!=0x4b)
    return {'status':'PASS' if passed else 'FAIL','scenario':scenario,'result_code':e.ota_result,
            'running_sha256':hashlib.sha256(running).hexdigest(),'candidate_sha256':hashlib.sha256(candidate).hexdigest(),
            'active_bank':hex(active_bank),'target_bank':hex(target),
            'active_boot_flag':hex(e.flash.data[active_bank+0x20]),
            'target_boot_flag':hex(e.flash.data[target+0x20]),'active_unchanged':unchanged,
            'real_crc_executed':0x20015de0 in e.covered,'instructions':e.total_instructions,
            'flash_operations':e.flash.operations,'no_device_access':True,
            'limitations':['仅执行 OTA END / CRC；候选数据预置于虚拟 NOR。',
                           '没有执行接收器、完整 USB 传输或真实 Flash。',
                           '没有证明启动失败后的强制恢复、自动回滚或掉电恢复。']}
