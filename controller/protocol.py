"""Pure packet codec and guarded client; importing this module opens no device."""
from dataclasses import dataclass

MAGIC = bytes([0xfe,0x53,0x4c])
VERSION = 1
INFO, FRAME, RELEASE, KEEPALIVE, TIMEOUT = range(5)
STATUS = {0:'OK',1:'协议版本不支持',2:'未知命令',3:'参数无效',4:'控制已超时或尚未激活',
          5:'当前版本仅支持 USB 模式控制',6:'报告长度必须为 32 字节'}


class ProtocolError(RuntimeError):
    pass


class UnsupportedFirmware(ProtocolError):
    pass


def packet(command,sequence=0,payload=b''):
    if type(command) is not int or not 0 <= command <= 4:
        raise ValueError('未知命令。')
    if type(sequence) is not int or not 0 <= sequence <= 255:
        raise ValueError('序号需要在 0～255 之间。')
    if not isinstance(payload,bytes) or len(payload)>27:
        raise ValueError('命令数据最多 27 字节。')
    if command==FRAME and len(payload)!=27:
        raise ValueError('一帧需要 9 组 RGB，共 27 字节。')
    return MAGIC+bytes([(VERSION<<4)|command,sequence])+payload+bytes(27-len(payload))


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


def decode(request,reply):
    if len(reply)!=32 or reply[:3]!=MAGIC or reply[17:21]!=b'Q101':
        raise UnsupportedFirmware('设备没有返回侧灯 HID 扩展标识；请使用对应实验固件。回复：'+reply.hex())
    if reply[3]!=(request[3]|8) or reply[4]!=request[4] or reply[6]!=VERSION:
        raise ProtocolError('回复版本、命令或序号不匹配。')
    if reply[5]:
        raise ProtocolError(STATUS.get(reply[5],'未知状态：%d'%reply[5]))
    if reply[7]!=9 or reply[21]!=162 or not reply[12]&1:
        raise UnsupportedFirmware('设备能力与此客户端不匹配。')
    return State(bool(reply[8]&1),bool(reply[8]&2),reply[9],
                 {0:'ble',1:'rf',2:'usb'}.get(reply[10],'unknown'),reply[11],
                 int.from_bytes(reply[13:15],'little'),int.from_bytes(reply[15:17],'little'),
                 bool(reply[12]&1),reply[7],reply[21])


class Client:
    def __init__(self,transport):
        self.transport=transport
        self.sequence=0
        self.verified=False
        self.connection=None

    def _request(self,command,payload=b''):
        self.sequence=(self.sequence+1)&255
        request=packet(command,self.sequence,payload)
        return decode(request,self.transport.request(request))

    def info(self):
        state=self._request(INFO)
        self.verified=True
        self.connection=state.connection
        return state

    def _require_usb(self):
        # Every new connection verifies the extension before sending any frame.
        if not self.verified:
            self.info()
        if self.connection!='usb':
            raise ProtocolError('请接数据线并切到 USB 模式；接收器目前只开放能力查询。')

    def frame(self,rgb):
        if not isinstance(rgb,bytes) or len(rgb)!=27:
            raise ValueError('需要 27 字节 RGB 帧。')
        self._require_usb()
        return self._request(FRAME,rgb)

    def release(self):
        self._require_usb()
        return self._request(RELEASE)

    def keepalive(self):
        self._require_usb()
        return self._request(KEEPALIVE)

    def timeout(self,milliseconds):
        if type(milliseconds) is not int or not 500 <= milliseconds <= 10000:
            raise ValueError('超时需要在 500～10000 毫秒之间。')
        self._require_usb()
        return self._request(TIMEOUT,milliseconds.to_bytes(2,'little'))
