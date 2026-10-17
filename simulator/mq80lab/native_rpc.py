"""JSON-lines transport for the native macOS app. No HTTP or device access."""
import argparse
import base64
import json
import os
import sys
import threading
import time
from pathlib import Path
from .server import Lab
from .profile import COVERAGE,layout
from .scenarios import BUILTINS


def dispatch(lab,method,params):
    if method=='bootstrap':
        return {'version':'2.4.1','firmwares':lab.catalog(),'layout':layout(lab.runtime.image),
                'scenarios':BUILTINS,'coverage':COVERAGE,'transport':'stdio-json-lines'}
    if method=='action':return lab.action(params)
    if method=='firmware':
        encoded=params.get('base64','')
        if not isinstance(encoded,str) or len(encoded)>180000:raise ValueError('固件超过 128 KB。')
        return lab.upload(base64.b64decode(encoded,validate=True),params.get('name','firmware.bin'))
    if method=='start_job':return lab.start_job(params)
    if method=='job':return lab.public_job(params['id'])
    with lab.lock:
        if method=='state':return lab.runtime.state()
        if method=='report':return lab.runtime.report()
        if method=='snapshot':return lab.runtime.snapshot()
        if method=='recording':return lab.recording
        if method=='memory':return lab.runtime.read_memory(int(str(params.get('address','0x80b80')),0),int(params.get('size',64)))
    raise ValueError('未知原生接口：'+str(method))


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--data-dir',type=Path,default=Path.home()/'Library/Application Support/MQ80 Firmware Lab')
    parser.add_argument('--parent-pid',type=int)
    args=parser.parse_args();lab=Lab(args.data_dir)
    if args.parent_pid:
        def supervise():
            while True:
                time.sleep(2)
                try:os.kill(args.parent_pid,0)
                except OSError:os._exit(0)
        threading.Thread(target=supervise,daemon=True).start()
    # Input is bounded before JSON parsing; stdout contains protocol only.
    while True:
        line=sys.stdin.buffer.readline(8*1024*1024+1)
        if not line:return
        identity=None
        try:
            if len(line)>8*1024*1024 or not line.endswith(b'\n'):raise ValueError('原生请求超过 8 MB 或不完整。')
            request=json.loads(line)
            if not isinstance(request,dict) or type(request.get('id')) is not int:raise ValueError('请求缺少整数 id。')
            identity=request['id'];params=request.get('params',{})
            if not isinstance(params,dict):raise ValueError('params 必须是对象。')
            result=dispatch(lab,request.get('method'),params)
            response={'id':identity,'ok':True,'result':result}
        except Exception as error:
            response={'id':identity,'ok':False,'error':{'message':str(error)}}
            with lab.lock:
                if lab.runtime.error_info:response['error']['state']=lab.runtime.state()
        print(json.dumps(response,ensure_ascii=False,separators=(',',':')),flush=True)


if __name__=='__main__':main()
