"""Cooperative, resumable real-code execution with explicit hardware boundaries.

The CPU is paused at the PM call until a modeled PAD / TIMER event arrives.
No original instruction bytes or input BIN files are overwritten by stubs.
"""
import base64
import copy
import hashlib
import json
import struct
import zlib
from dataclasses import asdict
from . import machine as m
from .profile import ASSETS, PROFILE, inspect_image, layout, COVERAGE
from .peripherals import Environment, NorFlash, COLUMN_PINS, crc16

MODES = {'ble':0,'rf':1,'usb':2}
FUNCTIONS = {'timer1_irq':0x37a8,'scanner':0x30bc,'debounce':0x20014398,
             'rf_task':0x2001273c,'ble_task':0x20011da4,'usb_reports':0x2001bda0,
             'frame':0x2000c400,'housekeeping':0x2001b2c8}
BOUNDARIES = {0x1508:'delay_us',0x14e4:'delay_ms',0x20014b20:'silicon_pm',
              0x6dec:'rf_tx',0x7244:'ble_sdk_loop',0x20017414:'ble_notify',
              0x2000d450:'usb_endpoint_send',0x200092e0:'flash_program',
              0x20009248:'flash_read',0x20009344:'flash_erase',0x2000f400:'ota_result'}
PASSIVE_MMIO = [(0x80100000,0x80102000),(0x80140000,0x80140060),
                (0x80140100,0x80140190),(0x80140200,0x80140220),
                (0x80140300,0x80140340),(0x80140800,0x80141000),
                (0xe4000000,0xe4004000),(0xe4200000,0xe4201000)]


class SimulationFault(RuntimeError):
    pass


def bounded_int(value,low,high,label):
    if type(value) is not int or not low <= value <= high:
        raise ValueError('%s 必须是 %d～%d 的整数。' % (label,low,high))
    return value


class Runtime(m.FirmwareEmulator):
    def __init__(self,image,environment=None,name='firmware.bin'):
        self.metadata = inspect_image(image,name)
        self.env = Environment()
        if environment: self.env.update(environment)
        self.now_us = 0
        self.keys = set()
        self.trace = []
        self.trace_dropped = 0
        self.sequence = 0
        self.reports = []
        self.packets = []
        self.host_text = ''
        self.last_host_hid = bytes(8)
        self.flash = NorFlash(image)
        self.call_context = None
        self.pending_driver = None
        self.pending_pm = None
        self.sdk_sleep = None
        self.rf_ack = None
        self.rf_frame_token = None
        self.rf_unique_count = 0
        self.rf_tx_attempts = 0
        self.error_info = None
        self.paused = False
        self.total_instructions = 0
        self.total_extensions = 0
        self.calls = {}
        self.boundary_counts = {}
        self.mmio_reads = {}
        self.mmio_writes = {}
        self.covered = set()
        self._last_hid = bytes(8)
        self._last_queue = '0000'
        self._last_frame_at = 0
        self._next_irq_us = 1000
        self._call_instructions = 0
        self.ota_result = None
        self._decoded_instructions = {}
        super().__init__(image)
        self.u.mem_map(0x80100000,0x2000)
        self.u.mem_map(0xe4000000,0x4000)
        self.u.mem_map(0xe4200000,0x1000)
        # The actual startup copy/zero loops, before the application's setup.
        self.invoke('startup_copy_bss',m.XIP+0x8e,end=m.XIP+0x174)
        self._initialize_checkpoint()
        self.invoke('lighting_initialize',m.XIP+0xd5b4)
        self.log('runtime','checkpoint','从已连接运行检查点启动',{'full_boot':False,'profile':PROFILE})
        self.render()

    def log(self,kind,origin,message,details=None):
        self.sequence += 1
        self.trace.append({'id':self.sequence,'time_ms':round(self.now_us/1000,3),
                           'kind':kind,'origin':origin,'message':message,'details':details or {}})
        if len(self.trace)>3000:
            self.trace.pop(0)
            self.trace_dropped += 1

    def _initialize_checkpoint(self):
        for address,value in {0x80994:MODES[self.env.connection],0x80a31:3,0x80a12:2,
                              0x80be3:10,0x809ae:3,0x80993:1,0x808d2:0,0x808d4:11,
                              0x809f0:0,0x809f1:0,0x809e2:0,0x80b2c:0,
                              0x80be1:int(self.env.connection=='usb' and self.env.cable)}.items(): self.byte(address,value)
        self.word(0x80944,48000000)
        self.word(0xbdc,0x20014b20)
        self.word(0x81ac0,0x12345678)
        self.word(0xbc0,0x200092e0)
        self.word(0xbc4,0x20009248)
        self.word(0x80b54,0)
        self.byte(0x80140140,0x40)
        self.byte(0x80964,1)
        self.byte(0x80b8e,64)
        self.apply_environment(asdict(self.env),record=False)
        if self.env.keymap_preset=='captured-q101':
            values=json.loads((ASSETS/'q101-captured-fn-layer.json').read_text())
            data=b''.join(int(value).to_bytes(2,'big') for value in values)
            self.u.mem_write(0x80300,data)
            self.u.mem_write(0x80500,data)
            self.log('nvm','model','加载历史 Q101 VIA Fn 层作为键位检查点',
                     {'source':'assets/q101-captured-fn-layer.json','fresh_device_read':False,'factory_default_proven':False})
        self.u.mem_protect(m.XIP,0x20000,m.un.UC_PROT_READ|m.un.UC_PROT_EXEC)

    def on_code(self,u,address,size,extra):
        # Unicorn may reuse a translated block containing a different later
        # stop address. Honor the current call boundary even for cached blocks.
        if self.call_context and address==self.call_context['end']:
            u.emu_stop()
            return
        self.total_instructions += 1
        self._call_instructions += 1
        self.covered.add(address)
        if self._call_instructions > 6000000:
            self.error = '本次调用超过 600 万条指令预算。'
            u.emu_stop()
            return
        if address in BOUNDARIES:
            self.pending_driver = address
            u.emu_stop()
            return
        # Decode stable executable memory once. Dynamic scratch RAM remains
        # uncached; CPU stores and snapshot restore invalidate relocated code.
        cacheable = address < 0x80000 or m.XIP <= address < m.XIP+0x20000
        decoded = self._decoded_instructions.get(address) if cacheable else None
        if decoded is None:
            half = int.from_bytes(u.mem_read(address,2),'little')
            if half & 3 == 3:
                word = int.from_bytes(u.mem_read(address,4),'little')
                decoded = ('word',word) if word & 0x7f in [0x0b,0x2b,0x5b,0x7f] else (None,0)
            elif half & 0xe083 == 0x8000:
                index = ((m.bits(half,8,8)<<11) | (m.bits(half,12,12)<<10)
                         | (m.bits(half,3,3)<<9) | (m.bits(half,9,9)<<8)
                         | (m.bits(half,5,6)<<6) | (m.bits(half,2,2)<<5)
                         | (m.bits(half,10,11)<<3) | (m.bits(half,4,4)<<2))
                decoded = ('execit',index)
            else: decoded = (None,0)
            if cacheable: self._decoded_instructions[address] = decoded
        kind,value = decoded
        if kind:
            if kind == 'execit':
                if value >= 0x158: raise SimulationFault('EXEC.IT 索引超出当前配置。')
                word = int.from_bytes(u.mem_read(value,4),'little')
                cursor = self.execute_extension(word,address)
                if cursor != address+4: raise SimulationFault('EXEC.IT 控制流未实现。')
                cursor = address+2
            else: cursor = self.execute_extension(value,address)
            self.extension_count += 1
            self.total_extensions += 1
            # Updating PC in the instruction hook skips the decoded extension;
            # this preserves its semantics without restarting Unicorn per opcode.
            u.reg_write(m.PC,cursor & 0xffffffff)

    def _invalidate_code(self,address,size):
        if address < 0x80000:
            for start in range(max(0,(address & ~1)-2),address+size,2):
                self._decoded_instructions.pop(start,None)

    def write_int(self,address,value,size):
        self._invalidate_code(address,size)
        super().write_int(address,value,size)

    def on_read(self,u,kind,address,size,value,extra):
        if address == 0x8010081e and getattr(self,'_raw_hid_fifo',None) is not None:
            if size != 1 or not self._raw_hid_fifo:
                raise SimulationFault('USB OUT FIFO 被超量读取。')
            self.u.mem_write(address,bytes([self._raw_hid_fifo.pop(0)]))
        if address == m.STIMER:
            self.clock(self.now_us*16)
        if address == 0x80140182:
            # Analog-register bridge completes immediately in this model.
            self.byte(address,self.byte(address)&~0x80)
        if address in [0x80140300+i*8 for i in range(5)]:
            self._gpio_input(address)
        if address >= 0x80000000:
            if not any(start <= address and address+size <= end for start,end in PASSIVE_MMIO):
                raise SimulationFault('未建模的寄存器读取：0x%08X。' % address)
            self.mmio_reads[address] = self.mmio_reads.get(address,0)+1
        super().on_read(u,kind,address,size,value,extra)

    def on_write(self,u,kind,address,size,value,extra):
        self._invalidate_code(address,size)
        if address >= 0x80000000:
            if not any(start <= address and address+size <= end for start,end in PASSIVE_MMIO):
                raise SimulationFault('未建模的寄存器写入：0x%08X。' % address)
            self.mmio_writes[address] = self.mmio_writes.get(address,0)+1
        super().on_write(u,kind,address,size,value,extra)

    def _gpio_input(self,address):
        pins = 255
        if address == 0x80140320:
            pins &= ~2
            if (6,14) in self.keys: pins |= 2
            # PE3 is the modeled USB cable / power input.
            if not self.env.cable: pins &= ~8
        if address == 0x80140308:
            pins &= ~0x40
            if (7,14) in self.keys: pins |= 0x40
        for row,col in self.keys:
            if row>5: continue
            port,bit = COLUMN_PINS[col]
            output_low = not self.byte(0x80140303+port*8) & (1<<bit)
            output_enabled = not self.byte(0x80140302+port*8) & (1<<bit)
            if output_low and output_enabled:
                if row==0 and address==0x80140320: pins &= ~1
                if 1<=row<=5 and address==0x80140318: pins &= ~(1<<(row+1))
        self.u.mem_write(address,bytes([pins]))

    def invoke(self,name,address,*args,end=m.STOP,registers=None,check_abi=True):
        if self.call_context:
            raise SimulationFault('CPU 正在等待外设响应，不能嵌套执行其它固件任务。')
        self.reset_registers()
        for i,value in enumerate(args): self.set_reg(10+i,value)
        for key,value in (registers or {}).items(): self.set_reg(key,value)
        before = {i:self.reg(i) for i in [1,2,*m.CALLEE]}
        self.call_context = {'name':name,'end':end,'before':before,'check_abi':check_abi}
        self.calls[name] = self.calls.get(name,0)+1
        self._call_instructions = 0
        self.writes,self.reads,self.visited = [],[],set()
        self.instruction_count = self.extension_count = 0
        self.error = None
        self.pending_extension = None
        self._drive(address)

    def _drive(self,cursor):
        try:
            while self.call_context:
                end = self.call_context['end']
                self.u.emu_start(cursor,end,count=6000001)
                if self.error: raise SimulationFault(self.error)
                if self.pending_driver:
                    address = self.pending_driver
                    self.pending_driver = None
                    cursor = self._hardware(address)
                    if cursor is None: return
                elif self.pending_extension:
                    word,pc,size = self.pending_extension
                    self.pending_extension = None
                    cursor = self.execute_extension(word,pc)
                    if size==2:
                        if cursor != pc+4: raise SimulationFault('EXEC.IT 控制流未实现。')
                        cursor = pc+2
                    self.extension_count += 1
                    self.total_extensions += 1
                else:
                    cursor = self.u.reg_read(m.PC)
                    if cursor != end:
                        raise SimulationFault('调用意外停止于 0x%08X。' % cursor)
                    if end==m.STOP and self.call_context['check_abi']:
                        for register,value in self.call_context['before'].items():
                            if self.reg(int(register)) != value:
                                raise SimulationFault('固件违反调用约定：x%d 未恢复。' % int(register))
                    self.call_context = None
                    self._observe()
                    return
                self.u.reg_write(m.PC,cursor & 0xffffffff)
        except Exception as error:
            self.error_info = {'message':str(error),'pc':'0x%08X' % self.u.reg_read(m.PC),
                               'task':self.call_context['name'] if self.call_context else None}
            self.paused = True
            self.log('fault','cpu','执行停止：'+str(error),self.error_info)
            self.call_context = None
            raise SimulationFault(str(error)) from error

    def _hardware(self,address):
        name = BOUNDARIES[address]
        self.boundary_counts[name] = self.boundary_counts.get(name,0)+1
        a0,a1,a2,a3 = [self.reg(i) for i in range(10,14)]
        ret = self.reg(1)
        if name in ['delay_us','delay_ms']:
            self.now_us += a0*(1000 if name=='delay_ms' else 1)
            self.clock(self.now_us*16)
        elif name=='silicon_pm':
            delta = ((a2-self.now_us*16) & 0xffffffff)//16
            self.pending_pm = {'return_pc':ret,'deadline_us':self.now_us+delta,
                               'wake_due_us':self.now_us+self.env.wake_latency_ms*1000 if self.keys else None,
                               'mode':a0,'sources':a1,'entered':not bool(self.keys)}
            self.log('sleep','model','等待 PAD / TIMER 外部事件',dict(self.pending_pm))
            return None
        elif name=='rf_tx':
            self._rf_transmit(a0)
            self.set_reg(10,0)
        elif name=='usb_endpoint_send':
            busy = not self.env.host_ready or self.env.host_suspended or not self.env.cable
            payload = bytes(self.u.mem_read(a1,min(a2,64)))
            if not busy:
                self._host_report('usb',payload,{'endpoint':a0,'boundary':hex(address)})
            self.set_reg(10,int(busy))
        elif name=='ble_sdk_loop':
            self.set_reg(10,0)
        elif name=='ble_notify':
            busy = not self.env.host_ready or self.env.ble_busy
            payload = bytes(self.u.mem_read(a2,min(a3,64)))
            if not busy:
                self._host_report('ble',payload,{'connection_handle':a0,'attribute':a1,'boundary':hex(address)})
            self.set_reg(10,int(busy))
        elif name=='flash_read':
            payload=self.flash.read(a0,a1)
            if getattr(self,'ota_readback_fault',False) and a0==self.ota_target and a1==16:
                payload=bytes([payload[0]^1])+payload[1:]
            self.u.mem_write(a2,payload)
            self.set_reg(10,0)
        elif name=='flash_program':
            self.flash.write(a0,bytes(self.u.mem_read(a2,a1)))
            self.log('flash','model','虚拟 NOR 写入',self.flash.operations[-1])
            self.set_reg(10,0)
        elif name=='flash_erase':
            self.flash.erase(a0)
            self.log('flash','model','虚拟 NOR 扇区擦除',self.flash.operations[-1])
            self.set_reg(10,0)
        elif name=='ota_result':
            self.ota_result = a0
            self.log('ota','firmware','OTA END 返回',{'result':a0,'reboot_emulated':False})
            return self.call_context['end']
        return ret

    def _rf_transmit(self,address):
        frame = bytes(self.u.mem_read(address,64))
        token = frame.hex()
        repeated = token == self.rf_frame_token
        if not repeated:
            self.rf_frame_token = token
            self.rf_unique_count += 1
        self.rf_tx_attempts += 1
        drop = bool(self.env.drop_every and self.rf_tx_attempts % self.env.drop_every==0)
        record = {'time_ms':round(self.now_us/1000,3),'bytes':token,'retry':repeated,
                  'modeled_drop':drop,'receiver_connected':self.env.receiver}
        self.packets.append(record)
        self.packets = self.packets[-1000:]
        if not repeated: self.log('tx','firmware','2.4G 封包提交到发送边界',record)
        if self.rf_ack is None and self.env.receiver and not drop:
            self.rf_ack = {'at_us':self.now_us+self.env.ack_delay_ms*1000,'frame':frame.hex()}

    def _process_ack(self):
        if not self.rf_ack or self.now_us<self.rf_ack['at_us'] or self.call_context: return
        packet = bytes.fromhex(self.rf_ack['frame'])
        self.rf_ack = None
        if not self.env.receiver: return
        # Only known 16-byte report records are decoded. Other packet families
        # remain observable raw data and cannot silently become keyboard input.
        payloads = []
        for i in range(3):
            block = packet[6+i*16:22+i*16]
            if len(block)==16 and block[4]==0x24 and block[5]==8 and crc16(block[:14])==int.from_bytes(block[14:16],'little'):
                if block[6:14] not in payloads: payloads.append(block[6:14])
        ack = bytearray(64)
        ack[5]=7
        ack[6:9]=b'\x57\x82\x00'
        ack[9:11]=crc16(ack[6:9]).to_bytes(2,'little')
        self.u.mem_write(0x8f000,bytes(ack))
        self.invoke('rf_ack_parser',0x1840,0x8f000)
        accepted = self.reg(10)==1
        self.log('ack','model','虚拟接收器 ACK 已交给原固件',{'accepted':accepted,'host_delivery_is_modeled':True})
        if accepted:
            for payload in payloads: self._host_report('rf',payload,{'crc16_checked':True,'receiver':'model'})
        self.rf_frame_token = None

    def _host_report(self,transport,payload,details):
        record = {'time_ms':round(self.now_us/1000,3),'transport':transport,
                  'hex':payload.hex(),'modeled_host':True,**details}
        self.reports.append(record)
        self.reports = self.reports[-1000:]
        self.log('hid','firmware+model','报告进入虚拟主机',record)
        if len(payload)==8:
            old = set(self.last_host_hid[2:])-{0}
            shift = payload[0]&0x22
            for code in payload[2:]:
                if code and code not in old:
                    if 4<=code<=29:
                        char=chr(97+code-4)
                        self.host_text += char.upper() if shift else char
                    elif code==0x2c: self.host_text += ' '
                    elif code==0x28: self.host_text += '\n'
                    elif code==0x2a: self.host_text=self.host_text[:-1]
            self.host_text=self.host_text[-4000:]
            self.last_host_hid=payload

    def _observe(self):
        hid = bytes(self.u.mem_read(0x80b80,8))
        if hid != self._last_hid:
            self.log('report','firmware','HID 缓冲区变化',{'hex':hid.hex()})
            self._last_hid=hid
        queue=bytes(self.u.mem_read(0x818c8,2)).hex()
        if queue != self._last_queue:
            self.log('queue','firmware','发送队列变化',{'head':self.byte(0x818c8),'tail':self.byte(0x818c9)})
            self._last_queue=queue

    def apply_environment(self,values,record=True):
        previous=self.env.connection
        previous_effect=self.env.main_effect
        candidate=Environment()
        candidate.update({**asdict(self.env),**values})
        if candidate.connection != previous or candidate.keymap_preset != self.env.keymap_preset:
            raise ValueError('连接模式切换需要重建运行环境。')
        if record and previous_effect != candidate.main_effect and self.call_context:
            raise ValueError('请先唤醒，再切换主灯效果。')
        self.env=candidate
        writes={0x80948:self.env.battery_percent,0x80b44:int(self.env.charging),
                0x80b78:self.env.host_leds,0x80ac4:self.env.host_leds,
                0x808cc:int(self.env.main_off),0x808b1:self.env.main_effect,
                0x808b2:self.env.main_brightness,0x808b3:self.env.main_speed,
                0x808ca:self.env.side_mode,0x808cd:self.env.side_brightness}
        for address,value in writes.items(): self.byte(address,value)
        if record and previous_effect != self.env.main_effect:
            self.invoke('lighting_initialize',m.XIP+0xd5b4)
        if record: self.log('environment','model','环境输入已更新',values)

    def set_keys(self,ids):
        if not isinstance(ids,list) or len(ids)>32: raise ValueError('一次最多模拟 32 个键。')
        valid={key['id'] for key in layout(self.image)}
        if any(key not in valid for key in ids): raise ValueError('按键不在官方矩阵布局中。')
        current={tuple(map(int,key.split(','))) for key in ids}
        if current != self.keys:
            self.log('key','model','矩阵输入改变',{'pressed':ids})
            added=current-self.keys
            self.keys=current
            if added and self.pending_pm and self.pending_pm['wake_due_us'] is None:
                self.pending_pm['wake_due_us']=self.now_us+self.env.wake_latency_ms*1000
            if added and self.sdk_sleep:
                self.sdk_sleep['wake_due_us']=self.now_us+self.env.wake_latency_ms*1000

    def tap(self,ids,hold_ms=50):
        bounded_int(hold_ms,1,1000,'按住时间')
        self.set_keys(ids)
        self.advance(hold_ms)
        self.set_keys([])
        self.advance(max(35,self.env.wake_latency_ms+20))

    def render(self):
        if self.call_context: return
        self.invoke('frame',FUNCTIONS['frame'])

    def receive_raw_hid(self,report,path='direct'):
        """Model a USB OUT FIFO; execute both original receive/dispatch paths.

        This does not emulate USB enumeration, endpoint electrical timing or
        receiver firmware. Short reports are accepted here for negative tests.
        """
        if not isinstance(report,bytes) or not 1 <= len(report) <= 32:
            raise ValueError('Raw HID 报告需要 1～32 字节。')
        if path not in ['direct','general']:
            raise ValueError('未知 USB 接收路径。')
        if self.env.connection != 'usb' or not self.env.cable or not self.env.host_ready or self.env.host_suspended:
            raise ValueError('需要可响应的虚拟 USB 主机。')
        if self.call_context:
            raise ValueError('请先结束当前外设等待。')
        start=len(self.reports)
        self._raw_hid_fifo=list(report)
        self.byte(0x80100816,len(report))
        self.byte(0x80100839,0x40)
        self.byte(0x80100803,0)
        try:
            if path=='general':
                self.invoke('usb_general_receive',m.XIP+0xde8c)
                # Model the W1C acknowledgement; pending data is now in RAM.
                self.byte(0x80100839,0)
            else:
                self.byte(0x80b5c,self.byte(0x80b5c)|8)
            self.invoke('raw_hid_dispatch',m.XIP+0xe190)
            if self._raw_hid_fifo:
                raise SimulationFault('USB OUT FIFO 没有被原接收路径完整消费。')
        finally:
            self._raw_hid_fifo=None
            self.byte(0x80100839,0)
        replies=[bytes.fromhex(r['hex']) for r in self.reports[start:]
                 if r.get('endpoint')==4 and len(bytes.fromhex(r['hex']))==32]
        if len(replies)!=1:
            raise SimulationFault('Raw HID 请求没有得到一个完整回复。')
        self.log('raw-hid','firmware+model','虚拟主机请求已执行真实接收和回复路径',
                 {'request':report.hex(),'reply':replies[0].hex(),'receive_path':path})
        return replies[0]

    def _animation_pulse(self):
        # Actual housekeeping basic block: main/side speed dividers and ticks.
        # The caller's 10 ms cadence remains an explicit environment assumption.
        self.invoke('animation_clock',m.XIP+0x1b300,end=m.XIP+0x1b398)
        self.byte(0x80b45,(self.byte(0x80b45)+1)&255)
        self._last_frame_at=self.now_us

    def _resume_sleep(self,reason):
        if self.pending_pm:
            pm=self.pending_pm
            self.pending_pm=None
            status=(0x40000000 if pm['entered'] else 0x80) | (8 if reason=='PAD' else 2)
            self.set_reg(10,status)
            self.log('wake','model','PM 环境响应：'+reason,{'status':hex(status),'silicon_sleep_proven':False})
            self._drive(pm['return_pc'])
        elif self.sdk_sleep:
            self.sdk_sleep=None
            self.byte(0x80a3c,1)
            self.word(0x80b30,0)
            self.log('wake','model','BLE SDK 模型交付 PAD 唤醒',{'full_sdk':False})

    def _tick(self):
        self._process_ack()
        if self.byte(0x80140140)&0x40:
            self.byte(0x80140143,2)
            self.invoke('timer1_irq',FUNCTIONS['timer1_irq'])
        mode=self.byte(0x80994)
        if mode != MODES[self.env.connection]:
            raise SimulationFault('固件已切换连接模式；请在环境栏重建目标模式，完整重连流程尚未建模。')
        task='rf_task' if mode==1 else 'ble_task' if mode==0 else 'usb_reports'
        self.invoke(task,FUNCTIONS[task])
        if self.call_context: return
        if mode==0 and self.byte(0x80b08) and not self.sdk_sleep:
            self.sdk_sleep={'wake_due_us':self.now_us+self.env.wake_latency_ms*1000 if self.keys else None}
            self.log('sleep','model','BLE SDK 休眠模型等待 PAD',{'full_sdk':False})
        if not self.byte(0x80b08):
            self.invoke('debounce',FUNCTIONS['debounce'])
            # Execute the fast-idle counter basic block with its real IRQ count
            # and lighting condition. No synthetic increment of the sleep counter.
            if self.byte(0x80be2)>=45:
                self.invoke('fast_idle_counter',m.XIP+0x1b5a8,end=m.XIP+0x1b5c8,registers={9:0x808b0})
            if self.now_us-self._last_frame_at>=10000:
                # Animation ticks are inputs from the unmodeled full housekeeping
                # task; explicitly modeled here, separate from the RGB renderer.
                self._animation_pulse()
                self.render()
        self._process_ack()

    def advance(self,milliseconds):
        bounded_int(milliseconds,0,5000,'推进时间')
        if self.error_info: raise SimulationFault('运行已停止，请重置或加载快照。')
        target=self.now_us+milliseconds*1000
        while self.now_us<target:
            if self.pending_pm or self.sdk_sleep:
                waiting=self.pending_pm or self.sdk_sleep
                due=waiting.get('wake_due_us')
                deadline=waiting.get('deadline_us')
                event=min([x for x in [due,deadline,target] if x is not None])
                self.now_us=max(self.now_us,event)
                self.clock(self.now_us*16)
                if due is not None and self.now_us>=due:
                    self._resume_sleep('PAD')
                elif deadline is not None and self.now_us>=deadline:
                    self._resume_sleep('TIMER')
                else: break
                self._next_irq_us=self.now_us+1000
            else:
                self.now_us=min(target,max(self.now_us+1,self._next_irq_us))
                self.clock(self.now_us*16)
                self._tick()
                self._next_irq_us=self.now_us+1000
        self.clock(self.now_us*16)

    def jump_clock(self,milliseconds):
        bounded_int(milliseconds,0,3600000,'时钟跳跃')
        if self.call_context: raise ValueError('等待 PM 时请使用推进时间。')
        self.now_us+=milliseconds*1000
        self.clock(self.now_us*16)
        self._next_irq_us=self.now_us+1000
        self._last_frame_at=self.now_us
        self.log('clock','model','时钟跳跃；未执行被跳过的 IRQ 和任务',{'milliseconds':milliseconds})
        self.render()

    def preview_advance(self,milliseconds):
        """Wall-clock lighting preview; never used by strict regression steps.

        Only quiescent time may be coalesced. Pending input, reports and PM
        continue through advance(), preserving the original event ordering.
        Idle IRQs/sleep accounting are deliberately not claimed by this mode.
        """
        bounded_int(milliseconds,0,5000,'预览时间')
        if self.error_info: raise SimulationFault('运行已停止，请重置或加载快照。')
        unsettled = (self.keys or self.call_context or self.sdk_sleep or self.rf_ack
                     or self.byte(0x818c8) != self.byte(0x818c9)
                     or any(self.u.mem_read(0x81cac,16)) or any(self.u.mem_read(0x81c94,16)))
        if unsettled:
            self.advance(milliseconds)
            return
        start = self.now_us
        target = self.now_us+milliseconds*1000
        while self._last_frame_at+10000 <= target:
            self.now_us=max(self.now_us,self._last_frame_at+10000)
            self.clock(self.now_us*16)
            self._animation_pulse()
            self.render()
        self.now_us=target
        self.clock(self.now_us*16)
        self._next_irq_us=self.now_us+1000
        if self.trace and self.trace[-1]['kind'] == 'preview':
            self.trace[-1]['details']['elapsed_ms'] += milliseconds
            self.trace[-1]['details']['end_ms'] = self.now_us/1000
        else:
            self.log('preview','clock-model','实时灯效预览合并空闲调度',
                     {'start_ms':start/1000,'end_ms':self.now_us/1000,
                      'elapsed_ms':milliseconds,'idle_irqs_executed':False,'sleep_timing_verified':False})
        self.render()

    def sleep_checkpoint(self,gate='fast'):
        if self.env.connection=='usb': raise ValueError('USB 主机挂起可单独设置；无线休眠场景不适用于 USB。')
        if self.keys or self.call_context: raise ValueError('请先松开所有键，并等待当前任务完成。')
        if gate not in ['fast','normal']: raise ValueError('未知休眠场景。')
        if gate=='fast':
            self.apply_environment({'main_off':True,'side_mode':4})
            self.word(0x80b30,100)
        else:
            self.word(0x80b30,0)
            self.word(0x80a64,600)
        self.log('checkpoint','model','加载休眠计数检查点',{'gate':gate,'elapsed_time_measured':False})
        self._tick()

    def state(self):
        rgb=bytes(self.u.mem_read(m.FRAME,279))
        colors=[list(rgb[i:i+3]) for i in range(0,279,3)]
        status='fault' if self.error_info else 'sleeping' if self.pending_pm or self.sdk_sleep else 'restoring' if self.byte(0x80b08) else 'ready'
        return {'firmware':self.metadata,'profile':PROFILE,'environment':asdict(self.env),
                'time_ms':round(self.now_us/1000,3),'status':status,'error':self.error_info,
                'keys':[','.join(map(str,k)) for k in sorted(self.keys)],
                'rgb':{'main':colors[:84],'side':colors[84:],'physical_main_mapping_verified':False},
                'hid':bytes(self.u.mem_read(0x80b80,8)).hex(),
                'matrix_raw':bytes(self.u.mem_read(0x81cac,16)).hex(),
                'matrix_stable':bytes(self.u.mem_read(0x81c94,16)).hex(),
                'host_text':self.host_text,'reports':self.reports[-30:],'packets':self.packets[-15:],
                'queue':{'head':self.byte(0x818c8),'tail':self.byte(0x818c9)},
                'power':{'prepared':self.byte(0x80b08),'tx_inhibit':self.byte(0x80aee),
                         'pad_flag':self.byte(0x80a3c),'fast_idle':self.word(0x80b30),
                         'idle_seconds':self.word(0x80a64),'timer1_enabled':bool(self.byte(0x80140140)&0x40),
                         'pending_pm':self.pending_pm,'silicon_sleep_proven':False},
                'battery':{'cache':self.byte(0x80948),'query_active':bool(self.byte(0x80b58)&2),
                           'query_remaining_ms':max(0,3000-(((self.now_us*16-self.word(m.TICK))&0xffffffff)//16000))
                           if self.byte(0x80b58)&2 and self.metadata['sha256']=='f3e86554be1ed5faf11750fceea85980c9ccd080912be4552e8317fb09ef7719' else None},
                'stats':{'instructions':self.total_instructions,'andes_extensions':self.total_extensions,
                         'covered_addresses':len(self.covered),'calls':self.calls,'boundaries':self.boundary_counts,
                         'trace_dropped':self.trace_dropped,'flash_operations':len(self.flash.operations)},
                'trace':self.trace[-100:],'coverage':COVERAGE}

    def report(self):
        return {'format':'mq80lab-report-v1','state':self.state(),'trace':self.trace,
                'trace_truncated_count':self.trace_dropped,'reports':self.reports,'packets':self.packets,
                'register_reads':{hex(k):v for k,v in self.mmio_reads.items()},
                'register_writes':{hex(k):v for k,v in self.mmio_writes.items()},
                'flash_operations':self.flash.operations,'no_device_access':True,
                'limitations':[item['detail'] for item in COVERAGE if item['level']!='real']}

    def read_memory(self,address,size=64):
        bounded_int(address,0,0xffffffff,'地址')
        bounded_int(size,1,256,'读取长度')
        data=bytes(self.u.mem_read(address,size))
        return {'address':hex(address),'bytes':data.hex(),'registers':{'x%d'%i:hex(self.reg(i)) for i in range(32)},
                'pc':hex(self.u.reg_read(m.PC))}

    def snapshot(self):
        regions=[(0,m.RAM_END),(0x80100000,0x2000),(0x80140000,0x4000),
                 (0xe4000000,0x4000),(0xe4200000,0x1000)]
        metadata={key:copy.deepcopy(getattr(self,key)) for key in ['now_us','trace','trace_dropped','sequence',
                  'reports','packets','host_text','call_context','pending_pm','sdk_sleep','rf_ack','rf_frame_token',
                  'rf_unique_count','rf_tx_attempts','error_info','paused','total_instructions','total_extensions','calls',
                  'boundary_counts','_last_queue','_last_frame_at','_next_irq_us','_call_instructions']}
        metadata.update(keys=[list(x) for x in self.keys],last_host_hid=self.last_host_hid.hex(),
                        last_hid=self._last_hid.hex(),covered=list(self.covered),
                        mmio_reads=self.mmio_reads,mmio_writes=self.mmio_writes)
        return {'format':'mq80lab-snapshot-v1','profile':PROFILE,'firmware_sha256':self.metadata['sha256'],
                'environment':asdict(self.env),'runtime':metadata,
                'regions':[[address,size,base64.b64encode(zlib.compress(bytes(self.u.mem_read(address,size)))).decode()]
                           for address,size in regions],
                'registers':[self.reg(i) for i in range(32)]+[self.u.reg_read(m.PC)],
                'flash':base64.b64encode(zlib.compress(bytes(self.flash.data))).decode(),
                'flash_operations':self.flash.operations}

    def restore(self,snapshot):
        if snapshot.get('format')!='mq80lab-snapshot-v1' or snapshot.get('profile')!=PROFILE:
            raise ValueError('快照格式或固件配置不兼容。')
        if snapshot.get('firmware_sha256')!=self.metadata['sha256']:
            raise ValueError('快照的固件 SHA256 与当前镜像不同。')
        expected=[(0,m.RAM_END),(0x80100000,0x2000),(0x80140000,0x4000),
                  (0xe4000000,0x4000),(0xe4200000,0x1000)]
        regions=snapshot.get('regions',[])
        if [(a,n) for a,n,_ in regions]!=expected: raise ValueError('快照内存区域不合法。')
        decoded=[]
        for address,size,value in regions:
            decoder=zlib.decompressobj()
            raw=decoder.decompress(base64.b64decode(value,validate=True),size+1)
            if len(raw)!=size or not decoder.eof: raise ValueError('快照内存数据不完整或过大。')
            decoded.append((address,raw))
        decoder=zlib.decompressobj()
        flash=decoder.decompress(base64.b64decode(snapshot['flash'],validate=True),0x100001)
        if len(flash)!=0x100000 or not decoder.eof: raise ValueError('快照 Flash 数据不完整。')
        registers=snapshot['registers']
        if len(registers)!=33 or any(type(x) is not int or not 0<=x<=0xffffffff for x in registers):
            raise ValueError('快照寄存器数据不合法。')
        restored=Environment()
        restored.update(snapshot['environment'])
        runtime=snapshot['runtime']
        required={'now_us','trace','trace_dropped','sequence','reports','packets','host_text','call_context',
                  'pending_pm','sdk_sleep','rf_ack','rf_frame_token','rf_unique_count','rf_tx_attempts','error_info','paused',
                  'total_instructions','total_extensions','calls','boundary_counts','_last_queue','_last_frame_at',
                  '_next_irq_us','_call_instructions','keys','last_host_hid','last_hid','covered','mmio_reads','mmio_writes'}
        if set(runtime)!=required: raise ValueError('快照运行状态字段不匹配。')
        for address,raw in decoded: self.u.mem_write(address,raw)
        self._decoded_instructions.clear()
        for i,value in enumerate(registers[:32]): self.set_reg(i,value)
        self.u.reg_write(m.PC,registers[32])
        self.env=restored
        for key,value in runtime.items():
            if key not in ['keys','last_host_hid','last_hid','covered','mmio_reads','mmio_writes']:
                setattr(self,key,value)
        self.keys={tuple(x) for x in runtime['keys']}
        self.last_host_hid=bytes.fromhex(runtime['last_host_hid'])
        self._last_hid=bytes.fromhex(runtime['last_hid'])
        self.covered=set(runtime['covered'])
        self.mmio_reads={int(k):v for k,v in runtime['mmio_reads'].items()}
        self.mmio_writes={int(k):v for k,v in runtime['mmio_writes'].items()}
        self.flash.data[:]=flash
        self.flash.operations=snapshot['flash_operations']
        self.log('snapshot','model','运行快照已恢复',{'same_firmware_sha256':True})
