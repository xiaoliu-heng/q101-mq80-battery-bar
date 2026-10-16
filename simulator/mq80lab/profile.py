import hashlib
import json
import struct
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / 'assets'
PROFILE = 'mq80-cs836b-v0109-fixed-map-v1'
KNOWN = {
    'd19733978808c856db93236ea3472b2cf843493c9aa6d5320db50412431e5210': 'MQ80 官方 V0109',
    'f3e86554be1ed5faf11750fceea85980c9ccd080912be4552e8317fb09ef7719': 'Fn+B 侧灯电量条 v2',
}


def inspect_image(image, name='firmware.bin'):
    if not isinstance(image, bytes) or not 0x1ed54 <= len(image) <= 0x20000:
        raise ValueError('需要 CS836B 应用 BIN，大小须在 126292～131072 字节之间。')
    if image[0x20:0x24] != b'KNLT':
        raise ValueError('镜像缺少 KNLT 标记；EXE 升级工具和其它机型不能在此运行。')
    if struct.unpack_from('<I', image, 0x18)[0] != len(image):
        raise ValueError('镜像头的 codesize 与实际文件大小不一致。')
    crc = zlib.crc32(image[:-4]) ^ 0xffffffff
    if struct.unpack_from('<I', image, len(image)-4)[0] != crc:
        raise ValueError('固件 CRC 校验失败，请先完成固件构建。')
    if (len(image)-4) % 16:
        raise ValueError('固件未按 OTA 的 16 字节要求对齐。')
    digest = hashlib.sha256(image).hexdigest()
    return {'id': digest, 'name': KNOWN.get(digest, Path(name).name), 'size': len(image),
            'sha256': digest, 'crc': '%08X' % crc, 'known': digest in KNOWN,
            'profile': PROFILE, 'compatibility': '已知镜像' if digest in KNOWN else '自定义镜像：固定地址兼容性待运行检查'}


LABELS = {0x28: 'Enter', 0x29: 'Esc', 0x2a: 'Backspace', 0x2b: 'Tab', 0x2c: 'Space',
          0x2d: '-', 0x2e: '=', 0x2f: '[', 0x30: ']', 0x31: '\\', 0x33: ';',
          0x34: "'", 0x35: '`', 0x36: ',', 0x37: '.', 0x38: '/', 0x39: 'Caps',
          0x46: 'PrtSc', 0x49: 'Ins', 0x4a: 'Home', 0x4b: 'PgUp', 0x4c: 'Del',
          0x4d: 'End', 0x4e: 'PgDn', 0x4f: '→', 0x50: '←', 0x51: '↓', 0x52: '↑',
          0xe0: 'Ctrl', 0xe1: 'Shift', 0xe2: 'Option', 0xe3: 'Command',
          0xe4: 'Ctrl R', 0xe5: 'Shift R', 0xe6: 'Option R', 0xe7: 'Command R'}
LABELS.update({i + 4: chr(65+i) for i in range(26)})
LABELS.update({i + 0x1e: str(i+1) for i in range(9)})
LABELS[0x27] = '0'
LABELS.update({0x3a+i: 'F%d' % (i+1) for i in range(12)})


def layout(image):
    definition = json.loads((ASSETS/'layout.json').read_text())
    result = []
    initialized = image[0x1e3a0:0x1ed50]
    for y, row in enumerate(definition['layouts']['keymap']):
        x, pending = 0.0, {}
        for item in row:
            if isinstance(item, dict):
                pending.update(item)
                continue
            r, c = map(int, item.split(','))
            x += pending.get('x', 0)
            width = pending.get('w', 1)
            code = int.from_bytes(initialized[0x200+(r*16+c)*2:0x202+(r*16+c)*2], 'big')
            label = 'Fn' if (r,c) == (7,14) else 'Esc' if (r,c) == (6,14) else LABELS.get(code, '%04X' % code)
            result.append({'id': item, 'row': r, 'col': c, 'x': x, 'y': y,
                           'w': width, 'label': label, 'keycode': code})
            x += width
            pending = {}
    return result


COVERAGE = [
    {'id':'cpu','name':'RV32 / Andes 执行','level':'real','detail':'真实镜像的已到达机器码；未知指令立即停止。不是完整芯片周期仿真。'},
    {'id':'startup','name':'启动与运行时','level':'partial','detail':'执行启动复制和 BSS 清零，随后从已连接检查点初始化；不执行完整上电、PLL 与配对过程。'},
    {'id':'matrix','name':'按键矩阵 / 消抖','level':'real','detail':'16 列真实 GPIO 驱动、8 行含 Esc / Fn 特殊输入；扫描、消抖、层和 HID 由固件执行。电气瞬态未建模。'},
    {'id':'rgb','name':'背光 / 侧灯','level':'real','detail':'帧与效果函数真实执行，输出 84+9 颗 LED 原始数据；动画节拍由模型推进，键帽与主灯索引的物理映射未校准。'},
    {'id':'clock','name':'时钟 / Timer1','level':'model','detail':'严格仿真采用 16 MHz STIMER、假定 1 ms IRQ 和离散调度。实时灯效预览在空闲时按墙钟合并渲染，不执行被跳过的 IRQ，不用于休眠计时断言；有按键、队列或 PM 等待时回到离散执行。'},
    {'id':'pm','name':'2.4G 休眠 / 唤醒','level':'partial','detail':'真实 PM 准备和返回代码；在 PM API 处暂停，PAD / TIMER 由环境响应。没有真实电流与保持域模型。'},
    {'id':'rf','name':'2.4G 接收器','level':'partial','detail':'真实报告排队与封包，模拟 ACK、延迟、断连与丢包；没有空口、RF IRQ 或接收器固件。'},
    {'id':'usb','name':'USB 主机','level':'partial','detail':'真实 USB 报告任务和 Raw HID 两条接收/分派路径；OUT FIFO 与端点发送边界由模型提供。可设置忙、挂起、主机 LED；枚举、完整控制传输和远程唤醒未建模。'},
    {'id':'ble','name':'Bluetooth 主机','level':'partial','detail':'真实应用任务、队列、通知提交边界；BLE SDK 调度、链路层、配对、加密与休眠由模型替代。'},
    {'id':'battery','name':'电池 / 充电','level':'model','detail':'向已定位电量缓存和充电输入注入测试值；没有复刻 ADC、锂电放电曲线或充电芯片。'},
    {'id':'flash','name':'NOR Flash / OTA','level':'partial','detail':'1 MB 内存 NOR、1→0 写入与扇区擦除；可执行真实 OTA END / CRC 路径。没有掉电、电气时序或 bootloader 自动恢复模型。'},
    {'id':'mmio','name':'寄存器 / 外设','level':'partial','detail':'已知 GPIO、定时器、DMA/SPI、射频配置寄存器采用锁存器模型；未知区域访问失败。PLL、DMA 完成中断等不具备完整行为。'},
]
