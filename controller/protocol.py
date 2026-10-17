"""Pure packet codec and guarded client; importing this module opens no device."""
from dataclasses import dataclass
from typing import Optional, Tuple
import secrets

MAGIC = bytes([0xfe,0x53,0x4c])
VERSION = 1
INFO, FRAME, RELEASE, KEEPALIVE, TIMEOUT, PLAY = range(6)
EFFECTS = {'solid':1, 'blink':2, 'breathe':3, 'rainbow':4}
FIRMWARE_FEATURES = {1:'fn_b_battery_bar',2:'battery_query',4:'usb_side_rgb',8:'rf_side_rgb',16:'local_effects'}
STATUS = {0:'OK',1:'协议版本不支持',2:'未知命令',3:'参数无效',4:'控制已超时或尚未激活',
          5:'当前连接方式不允许侧灯控制',6:'报告长度必须为 32 字节'}


class ProtocolError(RuntimeError):
    pass


class UnsupportedFirmware(ProtocolError):
    pass


def packet(command,sequence=0,payload=b''):
    if type(command) is not int or not 0 <= command <= 5:
        raise ValueError('未知命令。')
    if type(sequence) is not int or not 0 <= sequence <= 255:
        raise ValueError('序号需要在 0～255 之间。')
    if not isinstance(payload,bytes) or len(payload)>27:
        raise ValueError('命令数据最多 27 字节。')
    if command==FRAME and len(payload)!=27:
        raise ValueError('一帧需要 9 组 RGB，共 27 字节。')
    return MAGIC+bytes([(VERSION<<4)|command,sequence])+payload+bytes(27-len(payload))


def effect_payload(effect, color=b'\x00\x00\x00', brightness=25,
                   duration_ms=5000, period_ms=None, request_id=None):
    if effect not in EFFECTS or not isinstance(color,bytes) or len(color)!=3:
        raise ValueError('需要内置灯效名称及 3 字节 RGB。')
    if period_ms is None: period_ms=0 if effect=='solid' else 5000 if effect=='rainbow' else 1000
    if request_id is None: request_id=secrets.randbelow(0xffffffff)+1
    if (type(brightness) is not int or not 0 <= brightness <= 100 or
        type(duration_ms) is not int or not 100 <= duration_ms <= 60000 or
        type(period_ms) is not int or (period_ms!=0 if effect=='solid' else not 500 <= period_ms <= 20000) or
        type(request_id) is not int or not 1 <= request_id <= 0xffffffff or
        (effect=='rainbow' and color!=bytes(3))):
        raise ValueError('灯效参数超出范围。')
    return (bytes([EFFECTS[effect],brightness])+color+period_ms.to_bytes(2,'little')+
            duration_ms.to_bytes(2,'little')+request_id.to_bytes(4,'little'))


def matches_reply(request,reply):
    return (len(reply)==32 and reply[4]==request[4] and
            ((reply[:3]==MAGIC and reply[3]==(request[3]|8)) or
             (reply[0]==1 and reply[1]==request[1])))


@dataclass(frozen=True)
class State:
    active:bool
    battery_query:bool
    battery_percent:int
    connection:str
    last_frame_sequence:int
    timeout_ms:int
    remaining_ms:int
    usb_control:bool
    led_count:int
    maximum_channel:int
    rf_control:bool=False
    firmware_version:Optional[str]=None
    firmware_build:Optional[int]=None
    firmware_feature_flags:Optional[int]=None
    firmware_features:Tuple[str,...]=()
    native_effects:bool=False


def decode(request,reply):
    if len(reply)!=32 or reply[:3]!=MAGIC or reply[17:21]!=b'Q101':
        raise UnsupportedFirmware('设备没有返回侧灯 HID 扩展标识；请使用对应实验固件。回复：'+reply.hex())
    if reply[3]!=(request[3]|8) or reply[4]!=request[4] or reply[6]!=VERSION:
        raise ProtocolError('回复版本、命令或序号不匹配。')
    if reply[5]:
        raise ProtocolError(STATUS.get(reply[5],'未知状态：%d'%reply[5]))
    if reply[7]!=9 or reply[21]!=162 or not reply[12]&3:
        raise UnsupportedFirmware('设备能力与此客户端不匹配。')
    metadata = reply[23:26] == b'FV\x01'
    firmware_version = '.'.join(map(str,reply[26:29])) if metadata else None
    feature_flags = reply[29] if metadata else None
    return State(bool(reply[8]&1),bool(reply[8]&2),reply[9],
                 {0:'ble',1:'rf',2:'usb'}.get(reply[10],'unknown'),reply[11],
                 int.from_bytes(reply[13:15],'little'),int.from_bytes(reply[15:17],'little'),
                 bool(reply[12]&1),reply[7],reply[21],bool(reply[12]&2),
                 firmware_version,int.from_bytes(reply[30:32],'little') if metadata else None,
                 feature_flags,tuple(name for bit,name in FIRMWARE_FEATURES.items()
                                     if feature_flags is not None and feature_flags&bit),
                 bool(metadata and feature_flags&16 and reply[22]&16))


class Client:
    def __init__(self,transport):
        self.transport=transport
        self.sequence=0
        self.verified=False
        self.connection=None
        self.controls={}
        self.native_effects=False

    def _request(self,command,payload=b''):
        self.sequence=(self.sequence+1)&255
        request=packet(command,self.sequence,payload)
        return decode(request,self.transport.request(request))

    def info(self):
        state=self._request(INFO)
        self.verified=True
        self.connection=state.connection
        self.controls={'usb':state.usb_control,'rf':state.rf_control}
        self.native_effects=state.native_effects
        return state

    def _require_control(self):
        # Every new connection verifies the extension before sending any frame.
        if not self.verified:
            self.info()
        expected=getattr(self.transport,'connection',self.connection)
        if self.connection!=expected or not self.controls.get(self.connection,False):
            raise ProtocolError('固件未开放当前通道的侧灯控制；2.4G 需要 USB/RF 实验固件并切到 2.4G 模式。')

    def frame(self,rgb):
        if not isinstance(rgb,bytes) or len(rgb)!=27:
            raise ValueError('需要 27 字节 RGB 帧。')
        self._require_control()
        return self._request(FRAME,rgb)

    def release(self):
        self._require_control()
        return self._request(RELEASE)

    def play(self,effect,**parameters):
        payload=effect_payload(effect,**parameters)
        self._require_control()
        if not self.native_effects:
            raise UnsupportedFirmware('本地灯效需要 v3.1.0 或更新的兼容固件；不会回退到逐帧发送。')
        return self._request(PLAY,payload)

    def keepalive(self):
        self._require_control()
        return self._request(KEEPALIVE)

    def timeout(self,milliseconds):
        if type(milliseconds) is not int or not 500 <= milliseconds <= 10000:
            raise ValueError('超时需要在 500～10000 毫秒之间。')
        self._require_control()
        return self._request(TIMEOUT,milliseconds.to_bytes(2,'little'))
