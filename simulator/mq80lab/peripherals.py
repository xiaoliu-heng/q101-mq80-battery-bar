"""Deterministic environment models. None of these interfaces access hardware."""
from dataclasses import dataclass, asdict

COLUMN_PINS = [(4,4),(4,5),(4,6),(4,7),(0,0),(1,0),(1,1),(1,2),
               (1,3),(0,1),(0,2),(0,3),(0,4),(1,4),(1,5),(2,0)]


@dataclass
class Environment:
    connection: str = 'rf'
    keymap_preset: str = 'captured-q101'
    battery_percent: int = 88
    charging: bool = False
    cable: bool = False
    receiver: bool = True
    host_ready: bool = True
    host_suspended: bool = False
    host_leds: int = 0
    ack_delay_ms: int = 1
    drop_every: int = 0
    wake_latency_ms: int = 1
    ble_busy: bool = False
    main_off: bool = True
    main_effect: int = 1
    main_brightness: int = 5
    main_speed: int = 2
    side_mode: int = 4
    side_brightness: int = 3

    def update(self, values):
        fields = asdict(self)
        if not isinstance(values, dict) or set(values)-set(fields):
            raise ValueError('环境包含未知字段。')
        bounds = {'battery_percent':(0,100), 'host_leds':(0,31), 'ack_delay_ms':(0,1000),
                  'drop_every':(0,10000), 'wake_latency_ms':(0,1000), 'main_effect':(0,18),
                  'main_brightness':(0,9), 'main_speed':(0,4), 'side_mode':(0,4), 'side_brightness':(0,3)}
        new = {**fields, **values}
        if new['connection'] not in ['rf','usb','ble']:
            raise ValueError('连接方式必须是 rf / usb / ble。')
        if new['keymap_preset'] not in ['captured-q101','image']:
            raise ValueError('未知键位检查点。')
        for key,(low,high) in bounds.items():
            if type(new[key]) is not int or not low <= new[key] <= high:
                raise ValueError('%s 必须在 %d～%d 之间。' % (key,low,high))
        for key,value in fields.items():
            if type(value) is bool and type(new[key]) is not bool:
                raise ValueError('%s 必须是布尔值。' % key)
        for key,value in new.items():
            setattr(self,key,value)


def crc16(data):
    value = 0xffff
    for byte in data:
        value ^= byte
        for _ in range(8): value = (value>>1) ^ (0xa001 if value & 1 else 0)
    return value


class NorFlash:
    def __init__(self, image):
        self.data = bytearray(b'\xff' * 0x100000)
        self.data[:len(image)] = image
        self.operations = []

    def bounds(self, address, size):
        if address < 0 or size < 0 or address+size > len(self.data):
            raise ValueError('虚拟 Flash 访问越界。')

    def read(self, address, size):
        self.bounds(address,size)
        return bytes(self.data[address:address+size])

    def write(self, address, data):
        self.bounds(address,len(data))
        if len(data)>256: raise ValueError('虚拟 NOR 单页写入超过 256 字节。')
        if data and address//256 != (address+len(data)-1)//256:
            raise ValueError('虚拟 NOR 写入跨页。')
        self.data[address:address+len(data)] = bytes(a&b for a,b in zip(self.data[address:address+len(data)],data))
        self.operations.append({'op':'program','address':address,'size':len(data)})

    def erase(self,address):
        if address % 4096: raise ValueError('虚拟 NOR 擦除地址必须按 4K 对齐。')
        self.bounds(address,4096)
        self.data[address:address+4096] = b'\xff'*4096
        self.operations.append({'op':'erase','address':address,'size':4096})
