#!/usr/bin/env python3
"""Control the experimental Q101/MQ80 side light; firmware flashing is separate."""
import argparse
import colorsys
import json
import sys
from dataclasses import asdict
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from controller.protocol import Client,ProtocolError
from controller.transports import open_hardware,SimulatedTransport,TransportError


def color(text):
    try:
        value=bytes.fromhex(text.removeprefix('#'))
        if len(value)!=3:raise ValueError()
        return value
    except ValueError as e:
        raise argparse.ArgumentTypeError('颜色使用六位十六进制，例如 FF8000。') from e


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--simulate',action='store_true',help='只运行离线固件，无设备访问')
    parser.add_argument('--firmware',type=Path,help='仅用于 --simulate 的固件 BIN；不会刷写设备',
                        default=ROOT/'build/side-hid/MQ80-CS836B-V0109-20241220-BatteryBar-SideHID-EXPERIMENTAL.bin')
    parser.add_argument('--receiver',action='store_true',help='只查询 2.4G 接收器通道，不允许控制')
    sub=parser.add_subparsers(dest='command',required=True)
    sub.add_parser('info',help='读取扩展能力和固件电量缓存')
    sub.add_parser('release',help='立即恢复原灯效')
    for name,help_text in [('solid','整条灯显示一种颜色'),('frame','九段分别设置颜色'),('rainbow','电脑生成彩虹动画')]:
        p=sub.add_parser(name,help=help_text)
        if name=='solid':p.add_argument('color',type=color)
        if name=='frame':p.add_argument('colors',type=color,nargs=9)
        p.add_argument('--seconds',type=float,default=5)
        p.add_argument('--brightness',type=int,default=100)
        if name=='rainbow':p.add_argument('--fps',type=int,default=20)
    args=parser.parse_args(argv)
    if args.receiver and (args.command!='info' or args.simulate):
        parser.error('--receiver 仅用于真实接收器的 info 查询。')
    if hasattr(args,'seconds') and not 0 < args.seconds <= 3600:
        parser.error('--seconds 需要大于 0 且不超过 3600。')
    if hasattr(args,'brightness') and not 0 <= args.brightness <= 100:
        parser.error('--brightness 需要在 0～100 之间。')
    if hasattr(args,'fps') and not 1 <= args.fps <= 30:
        parser.error('--fps 需要在 1～30 之间。')
    transport=None
    started=False
    client=None
    try:
        if args.simulate:
            sys.path.insert(0,str(ROOT/'simulator'))
            transport=SimulatedTransport(args.firmware.read_bytes())
        else:transport=open_hardware(args.receiver)
        client=Client(transport)
        state=client.info()
        print(json.dumps({'mode':'offline' if args.simulate else 'hardware','state':asdict(state)},ensure_ascii=False),flush=True)
        if args.command=='info':return 0
        if args.command=='release':
            print(json.dumps(asdict(client.release()),ensure_ascii=False));return 0
        scale=lambda data:bytes(c*args.brightness//100 for c in data)
        elapsed=0.0
        while elapsed < args.seconds:
            if args.command=='rainbow':
                rgb=b''.join(bytes(round(c*255) for c in colorsys.hsv_to_rgb((i/9+elapsed*.2)%1,1,1)) for i in range(9))
                interval=min(1/args.fps,args.seconds-elapsed)
                client.frame(scale(rgb));started=True
            else:
                interval=min(.25,args.seconds-elapsed)
                if not started:
                    rgb=args.color*9 if args.command=='solid' else b''.join(args.colors)
                    client.frame(scale(rgb));started=True
                else:client.keepalive()
            transport.elapse(interval)
            elapsed+=interval
        if args.simulate:
            print(json.dumps({'side_rgb':transport.runtime.state()['rgb']['side']},ensure_ascii=False))
        return 0
    except KeyboardInterrupt:
        return 130
    except (OSError,ProtocolError,TransportError,ValueError) as e:
        print(str(e),file=sys.stderr);return 1
    finally:
        if started and client:
            try:client.release()
            except (OSError,RuntimeError):pass # Lease expires if disconnected.
        if transport:transport.close()


if __name__=='__main__':raise SystemExit(main())
