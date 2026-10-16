"""macOS IOKit transport, optional hidapi transport, and offline transport.

No transport is opened at import time. Only USB Raw HID usage FF60:61 is used;
this module has no OTA, flash, keyboard-report or system-key injection commands.
"""
import ctypes as C
import sys
import time
from .protocol import matches_reply

VID, KEYBOARD_PID, RECEIVER_PID = 0x320f,0x5055,0x5088


class TransportError(RuntimeError):
    pass


class MacTransport:
    def __init__(self,receiver=False):
        if sys.platform!='darwin':
            raise TransportError('IOKit 传输仅支持 macOS。')
        self.device=self.manager=self.device_set=self.dictionary=None
        self.references=[]
        self.received=[]
        self.cf=C.CDLL('/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation')
        self.io=C.CDLL('/System/Library/Frameworks/IOKit.framework/IOKit')
        P,L,I,U=C.c_void_p,C.c_long,C.c_int32,C.c_uint32

        def bind(lib,name,result,arguments):
            f=getattr(lib,name);f.restype=result;f.argtypes=arguments
            return f
        self.release=bind(self.cf,'CFRelease',None,[P])
        string=bind(self.cf,'CFStringCreateWithCString',P,[P,C.c_char_p,U])
        number=bind(self.cf,'CFNumberCreate',P,[P,I,P])
        dictionary=bind(self.cf,'CFDictionaryCreate',P,[P,P,P,L,P,P])
        count=bind(self.cf,'CFSetGetCount',L,[P])
        values=bind(self.cf,'CFSetGetValues',None,[P,P])
        self.run=bind(self.cf,'CFRunLoopRunInMode',I,[P,C.c_double,C.c_bool])
        self.loop=bind(self.cf,'CFRunLoopGetCurrent',P,[])()
        self.mode=string(None,b'kCFRunLoopDefaultMode',0x08000100)
        self.references.append(self.mode)
        create=bind(self.io,'IOHIDManagerCreate',P,[P,U])
        matching=bind(self.io,'IOHIDManagerSetDeviceMatching',None,[P,P])
        open_manager=bind(self.io,'IOHIDManagerOpen',I,[P,U])
        copy=bind(self.io,'IOHIDManagerCopyDevices',P,[P])
        open_device=bind(self.io,'IOHIDDeviceOpen',I,[P,U])
        self.close_device=bind(self.io,'IOHIDDeviceClose',I,[P,U])
        self.close_manager=bind(self.io,'IOHIDManagerClose',I,[P,U])
        schedule=bind(self.io,'IOHIDDeviceScheduleWithRunLoop',None,[P,P,P])
        self.unschedule=bind(self.io,'IOHIDDeviceUnscheduleFromRunLoop',None,[P,P,P])
        self.set_report=bind(self.io,'IOHIDDeviceSetReport',I,[P,I,L,P,L])
        callback_type=C.CFUNCTYPE(None,P,I,P,I,U,P,L)
        register=bind(self.io,'IOHIDDeviceRegisterInputReportCallback',None,[P,P,L,callback_type,P])
        props={'VendorID':VID,'ProductID':RECEIVER_PID if receiver else KEYBOARD_PID,
               'PrimaryUsagePage':0xff60,'PrimaryUsage':0x61}
        keys=[string(None,k.encode(),0x08000100) for k in props]
        storage=[L(v) for v in props.values()]
        vals=[number(None,4,C.byref(v)) for v in storage]
        self.references+=keys+vals
        key_callbacks=C.addressof((C.c_byte*48).in_dll(self.cf,'kCFTypeDictionaryKeyCallBacks'))
        val_callbacks=C.addressof((C.c_byte*40).in_dll(self.cf,'kCFTypeDictionaryValueCallBacks'))
        try:
            self.dictionary=dictionary(None,(P*len(keys))(*keys),(P*len(vals))(*vals),
                                       len(keys),key_callbacks,val_callbacks)
            self.manager=create(None,0)
            matching(self.manager,self.dictionary)
            self._check(open_manager(self.manager,0),'打开 HID 管理器')
            self.device_set=copy(self.manager)
            size=count(self.device_set) if self.device_set else 0
            if size!=1:
                raise TransportError('需要恰好一个目标 Raw HID 接口，当前找到 %d 个。'%size)
            devices=(P*size)();values(self.device_set,devices)
            candidate=devices[0]
            self._check(open_device(candidate,0),'打开 Raw HID 接口')
            self.device=candidate
            schedule(self.device,self.loop,self.mode)
            self.buffer=(C.c_ubyte*64)()

            @callback_type
            def receive(context,status,sender,kind,report_id,buffer,length):
                if status==0 and 0 < length <= 64:
                    self.received.append(bytes(C.string_at(buffer,length)))
            self.callback=receive
            register(self.device,self.buffer,64,self.callback,None)
        except Exception:
            self.close()
            raise

    @staticmethod
    def _check(code,action):
        if code:
            raise TransportError('%s失败：0x%08X。请关闭占用设备的 VIA 窗口后重试。'%(action,code&0xffffffff))

    def request(self,packet):
        if len(packet)!=32:
            raise ValueError('报告必须为 32 字节。')
        self.run(self.mode,0.001,True)
        self.received.clear()
        buffer=(C.c_ubyte*32).from_buffer_copy(packet)
        self._check(self.set_report(self.device,1,0,buffer,32),'发送 Raw HID 请求')
        deadline=time.monotonic()+1.0
        while time.monotonic()<deadline:
            while self.received:
                reply=self.received.pop(0)
                if matches_reply(packet,reply):return reply
            self.run(self.mode,0.01,True)
        raise TransportError('等待 HID 回复超时。')

    def close(self):
        if self.device:
            self.unschedule(self.device,self.loop,self.mode)
            self.close_device(self.device,0)
            self.device=None
        if self.manager:self.close_manager(self.manager,0)
        for name in ['device_set','manager','dictionary']:
            ref=getattr(self,name,None)
            if ref:self.release(ref);setattr(self,name,None)
        for ref in self.references:self.release(ref)
        self.references=[]

    def elapse(self,seconds):
        time.sleep(seconds)


class HidApiTransport:
    def __init__(self,receiver=False):
        try:import hid
        except ImportError as e:
            raise TransportError('Windows/Linux 需安装 hidapi：python -m pip install hidapi') from e
        pid=RECEIVER_PID if receiver else KEYBOARD_PID
        devices=[d for d in hid.enumerate(VID,pid)
                 if d.get('usage_page')==0xff60 and d.get('usage')==0x61]
        if len(devices)!=1:
            raise TransportError('需要恰好一个目标 Raw HID 接口，当前找到 %d 个。'%len(devices))
        self.device=hid.device()
        self.device.open_path(devices[0]['path'])

    def request(self,packet):
        # hidapi requires the leading zero Report ID for this unnumbered report.
        written=self.device.write(b'\0'+packet)
        if written!=33:raise TransportError('HID 写入不完整。')
        deadline=time.monotonic()+1.0
        while time.monotonic()<deadline:
            reply=bytes(self.device.read(32,timeout_ms=50))
            if matches_reply(packet,reply):return reply
        raise TransportError('等待 HID 回复超时。')

    def close(self):self.device.close()
    def elapse(self,seconds):time.sleep(seconds)


class SimulatedTransport:
    def __init__(self,firmware):
        from mq80lab.engine import Runtime
        self.runtime=Runtime(firmware,{'connection':'usb','cable':True})

    def request(self,packet):
        reply=self.runtime.receive_raw_hid(packet)
        self.runtime.render()
        return reply

    def elapse(self,seconds):self.runtime.jump_clock(round(seconds*1000))
    def close(self):pass


def open_hardware(receiver=False):
    return MacTransport(receiver) if sys.platform=='darwin' else HidApiTransport(receiver)
