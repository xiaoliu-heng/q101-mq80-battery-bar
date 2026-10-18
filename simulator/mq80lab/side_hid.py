"""Virtual host for the side-light extension. All frames go through real Raw HID.

Only the pure packet codec is shared with the physical controller. This module
never imports a hardware transport or opens a device.
"""
import colorsys
import sys
from pathlib import Path

if not getattr(sys, 'frozen', False):
    # The source simulator also runs with simulator/ as its working directory.
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from controller.protocol import Client, FRAME

HOST = 0x8269c
MODES = ['off', 'solid', 'frame', 'rainbow']


def integer(value, minimum, maximum, label):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError('%s需要在 %d～%d 之间。' % (label, minimum, maximum))
    return value


def validate_settings(settings):
    if not isinstance(settings, dict) or set(settings) - {'mode', 'colors', 'brightness', 'period_ms'}:
        raise ValueError('侧灯控制包含未知参数。')
    mode = settings.get('mode')
    if mode not in ['solid', 'frame', 'rainbow', 'release', 'stop']:
        raise ValueError('未知侧灯控制模式。')
    brightness = integer(settings.get('brightness', 100), 0, 100, '亮度')
    period = integer(settings.get('period_ms', 5000), 500, 20000, '彩虹周期（毫秒）')
    colors = settings.get('colors', [[255, 128, 0]] * 9)
    if not isinstance(colors, list) or len(colors) != 9:
        raise ValueError('侧灯需要九组 RGB 颜色。')
    for rgb in colors:
        if not isinstance(rgb, list) or len(rgb) != 3:
            raise ValueError('每个侧灯需要 R、G、B 三个通道。')
        for value in rgb:
            integer(value, 0, 255, 'RGB 通道')
    return {'mode': mode, 'colors': [rgb[:] for rgb in colors],
            'brightness': brightness, 'period_ms': period}


class SideHIDHost:
    def __init__(self, runtime):
        self.runtime = runtime
        self.client = Client(self)
        self.mode = 'off'
        self.colors = [[255, 128, 0] for _ in range(9)]
        self.brightness = 100
        self.period_ms = 5000
        self.started_us = 0
        self.next_send_us = 0
        self.frames_sent = 0

    @property
    def supported(self):
        return self.runtime.metadata['side_hid']

    @property
    def connection(self):
        return self.runtime.env.connection

    @property
    def usb_ready(self):
        r = self.runtime
        return (r.env.connection == 'usb' and r.env.cable and r.env.host_ready
                and not r.env.host_suspended and not r.call_context)

    @property
    def rf_ready(self):
        r = self.runtime
        return (r.metadata['side_hid_rf'] and self.connection == 'rf' and r.env.receiver
                and r.env.host_ready and not r.env.host_suspended and not r.call_context)

    @property
    def control_ready(self):
        return self.usb_ready or self.rf_ready

    def request(self, report):
        reply = (self.runtime.receive_rf_hid(report) if self.connection == 'rf'
                 else self.runtime.receive_raw_hid(report))
        if reply is None:
            raise ValueError('无线配置未收到回复。')
        self.runtime.render()
        if (report[3] & 7) == FRAME:
            self.frames_sent += 1
        return reply

    def _frame(self, settings, started):
        colors = settings['colors']
        if settings['mode'] == 'rainbow':
            phase = (self.runtime.now_us - started) / (settings['period_ms'] * 1000)
            colors = [[round(c * 255) for c in colorsys.hsv_to_rgb((i / 9 + phase) % 1, 1, 1)]
                      for i in range(9)]
        elif settings['mode'] == 'solid':
            colors = [colors[0]] * 9
        return bytes(c * settings['brightness'] // 100 for rgb in colors for c in rgb)

    def configure(self, settings):
        settings = validate_settings(settings)
        mode = settings['mode']
        if mode == 'stop':
            self.mode = 'off'
            return
        if not self.supported:
            raise ValueError('请先载入内置的侧灯 HID 实验固件。')
        if not self.control_ready:
            raise ValueError('需要虚拟 USB 主机，或 USB/2.4G 实验固件与在线接收器；休眠时请先按键唤醒。')
        if self.client.connection != self.connection:
            self.client = Client(self)
        if mode == 'release':
            self.client.release()
            self.mode = 'off'
        else:
            started = self.started_us if self.mode == mode else self.runtime.now_us
            self.client.frame(self._frame(settings, started))
            self.mode = mode
            self.colors = settings['colors']
            self.brightness = settings['brightness']
            self.period_ms = settings['period_ms']
            self.started_us = started
            self.next_send_us = self.runtime.now_us + self.interval_us
        self.runtime.log('side-hid-host', 'host-model', '虚拟电脑侧灯控制已更新', settings)

    @property
    def interval_us(self):
        return (100000 if self.connection == 'rf' else 50000) if self.mode == 'rainbow' else 250000

    def pump(self):
        if self.client.verified and self.client.connection != self.connection:
            self.mode = 'off'
        if self.mode == 'off' or not self.control_ready or self.runtime.now_us < self.next_send_us:
            return
        self.client.frame(self._frame(self.settings(), self.started_us))
        self.next_send_us = self.runtime.now_us + self.interval_us

    def settings(self):
        return {'mode': self.mode, 'colors': [rgb[:] for rgb in self.colors],
                'brightness': self.brightness, 'period_ms': self.period_ms}

    def state(self):
        # A pure observation of the fixed-map firmware state: polling the UI
        # cannot send packets, renew a lease or alter a saved execution point.
        remaining = 0
        sequence = 0
        timeout_ms = 2000
        if self.supported:
            r = self.runtime
            limit = r.word(HOST + 4) or 32000000
            timeout_ms = limit // 16000
            elapsed = (r.now_us * 16 - r.word(HOST)) & 0xffffffff
            owner = r.byte(HOST + 39) if r.metadata['side_hid_rf'] else 2
            if (r.byte(HOST + 35) and {'usb':2, 'rf':1}.get(self.connection) == owner
                    and elapsed < limit):
                remaining = (limit - elapsed + 15999) // 16000
            sequence = r.byte(HOST + 36)
        return {**self.settings(), 'supported': self.supported, 'usb_ready': self.usb_ready,
                'rf_supported': self.runtime.metadata['side_hid_rf'], 'rf_ready': self.rf_ready,
                'control_ready': self.control_ready,
                'active': remaining > 0, 'remaining_ms': remaining, 'timeout_ms': timeout_ms,
                'frames_sent': self.frames_sent, 'last_sequence': sequence,
                'fps': 1000000 // self.interval_us if self.mode != 'off' else 0,
                'transport': 'virtual-' + self.connection, 'no_device_access': True}

    def snapshot(self):
        return {**self.settings(), 'started_us': self.started_us, 'next_send_us': self.next_send_us,
                'frames_sent': self.frames_sent, 'sequence': self.client.sequence,
                'verified': self.client.verified, 'connection': self.client.connection}

    def restore(self, saved):
        expected = {'mode', 'colors', 'brightness', 'period_ms', 'started_us', 'next_send_us',
                    'frames_sent', 'sequence', 'verified', 'connection'}
        if not isinstance(saved, dict) or set(saved) != expected or saved['mode'] not in MODES:
            raise ValueError('快照侧灯主机状态无效。')
        settings = validate_settings({k: saved[k] for k in ['mode', 'colors', 'brightness', 'period_ms']}
                                     | {'mode': 'stop' if saved['mode'] == 'off' else saved['mode']})
        for key in ['started_us', 'next_send_us', 'frames_sent']:
            integer(saved[key], 0, 2**63 - 1, '侧灯快照计数')
        integer(saved['sequence'], 0, 255, '侧灯快照序号')
        if type(saved['verified']) is not bool or saved['connection'] not in [None, 'usb', 'rf']:
            raise ValueError('快照侧灯协议状态无效。')
        self.mode = saved['mode']
        self.colors = settings['colors']
        self.brightness = settings['brightness']
        self.period_ms = settings['period_ms']
        for key in ['started_us', 'next_send_us', 'frames_sent']:
            setattr(self, key, saved[key])
        self.client.sequence = saved['sequence']
        self.client.verified = saved['verified']
        self.client.connection = saved['connection']
        self.client.controls = {'usb': self.supported, 'rf': self.runtime.metadata['side_hid_rf']}
