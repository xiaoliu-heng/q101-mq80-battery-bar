import argparse
import copy
import hashlib
import json
import mimetypes
import os
import secrets
import signal
import sys
import threading
import time
import uuid
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit,parse_qs,unquote
from .engine import Runtime
from .profile import ASSETS,ROOT,COVERAGE,layout,inspect_image,SIDE_HID_IMAGE,SIDE_HID_RF_IMAGE,SIDE_HID_V3_IMAGE,SIDE_HID_V301_IMAGE,SIDE_HID_CURRENT_IMAGE
from .scenarios import BUILTINS,run_scenario,validate_scenario,apply_step
from .ota import run_ota


class Lab:
    def __init__(self,data_dir):
        self.data_dir=Path(data_dir)
        for name in ['firmware','reports','scenarios']:(self.data_dir/name).mkdir(parents=True,exist_ok=True)
        self.lock=threading.RLock()
        self.firmwares={}
        for path in [ASSETS/'stock.bin',ASSETS/'battery-bar-v2.bin',SIDE_HID_IMAGE,SIDE_HID_RF_IMAGE,SIDE_HID_V3_IMAGE,SIDE_HID_V301_IMAGE,SIDE_HID_CURRENT_IMAGE,*sorted((self.data_dir/'firmware').glob('*.bin'))]:
            try:
                image=path.read_bytes()
                meta=inspect_image(image,path.name)
                self.firmwares[meta['id']]={'meta':meta,'image':image}
            except (ValueError,OSError): continue
        image=(ASSETS/'battery-bar-v2.bin').read_bytes()
        self.runtime=Runtime(image)
        self.recording={'id':'recorded','name':'录制的运行步骤','environment':asdict(self.runtime.env),'steps':[]}
        self.jobs={}

    def catalog(self):
        return [item['meta'] for item in self.firmwares.values()]

    def image(self,key):
        if key not in self.firmwares: raise ValueError('请选择已导入的固件。')
        return self.firmwares[key]['image']

    def reset(self,key,environment):
        fresh=Runtime(self.image(key),environment)
        self.runtime=fresh
        self.recording={'id':'recorded','name':'录制的运行步骤','environment':asdict(fresh.env),'steps':[]}

    def upload(self,data,name):
        meta=inspect_image(data,name)
        with self.lock:
            (self.data_dir/'firmware'/(meta['id']+'.bin')).write_bytes(data)
            self.firmwares[meta['id']]={'meta':meta,'image':data}
        return meta

    def action(self,payload):
        if not isinstance(payload,dict):raise ValueError('请求必须是 JSON 对象。')
        action=payload.get('action')
        with self.lock:
            r=self.runtime
            if action=='reset': self.reset(payload['firmware'],payload.get('environment'))
            elif action=='restore':
                fresh=Runtime(r.image)
                fresh.restore(payload['snapshot'])
                self.runtime=fresh
                self.recording={'id':'after-restore','name':'恢复快照后的操作（需原快照）',
                                'environment':asdict(fresh.env),'steps':[]}
            elif action=='load_job':
                job=self.jobs.get(payload.get('job'))
                if not job or job['status']!='complete' or '_snapshot' not in job:
                    raise ValueError('此任务没有可载入的运行结果。')
                fresh=Runtime(self.image(job['_firmware']))
                fresh.restore(job['_snapshot'])
                self.runtime=fresh
                self.recording=copy.deepcopy(job['_scenario'])
            elif action in ['advance','jump','keys','tap','sleep','render','query','environment','preview','side_hid']:
                parameters={key:value for key,value in payload.items() if key!='action'}
                step={'type':action,**parameters}
                # Validate the public step schema before any runtime mutation.
                validate_scenario({'name':'interactive','steps':[step]})
                apply_step(r,step)
                steps=self.recording['steps']
                if (action in ['advance','preview'] and steps and steps[-1]['type']==action
                    and steps[-1]['ms']+step['ms']<=5000): steps[-1]['ms']+=step['ms']
                else: steps.append(step)
            else: raise ValueError('未知操作。')
            return self.runtime.state()

    def start_job(self,payload):
        if not isinstance(payload,dict):raise ValueError('任务必须是 JSON 对象。')
        with self.lock:
            if any(x['status']=='running' for x in self.jobs.values()): raise ValueError('已有回归任务在运行。')
            kind=payload.get('kind','scenario')
            if kind not in ['scenario','suite','compare','ota']:raise ValueError('未知测试任务。')
            firmware=payload.get('firmware',self.runtime.metadata['id'])
            self.image(firmware)
            scenario=None
            if kind in ['scenario','compare']:
                scenario=payload.get('scenario')
                if isinstance(scenario,str):scenario=next((x for x in BUILTINS if x['id']==scenario),None)
                scenario=validate_scenario(scenario)
            other=payload.get('other')
            if kind in ['compare','ota']:self.image(other)
            job_id=uuid.uuid4().hex
            job={'id':job_id,'kind':kind,'status':'running','completed':0,'total':1,
                 'current':'正在初始化','result':None,'error':None}
            self.jobs[job_id]=job

        def run():
            try:
                image=self.image(firmware)
                if kind=='ota':
                    job['current']='执行真实 OTA END / CRC（仅虚拟 Flash）'
                    result=run_ota(image,self.image(other),payload.get('ota_case','valid_image'))
                elif kind=='suite':
                    job['total']=len(BUILTINS)
                    rows=[]
                    for index,case in enumerate(BUILTINS):
                        job['current']=case['name']
                        runtime,row=run_scenario(image,case)
                        rows.append(row)
                        job['completed']=index+1
                    result={'status':'PASS' if all(x['status']=='PASS' for x in rows) else 'FAIL',
                            'passed':sum(x['status']=='PASS' for x in rows),'total':len(rows),'rows':rows,
                            'firmware_sha256':firmware,'no_device_access':True}
                elif kind=='compare':
                    job['total']=2
                    job['current']='运行第一个镜像'
                    left,a=run_scenario(image,scenario)
                    job['completed']=1
                    job['current']='运行对照镜像'
                    right,b=run_scenario(self.image(other),scenario)
                    result={'left':a,'right':b,'differences':[],
                            'meaning':'相同环境下的软件观察差异，不自动将差异判为缺陷。'}
                    for field in ['hid','host_text','rgb','battery','queue','power']:
                        if a['state'][field]!=b['state'][field]:
                            result['differences'].append({'field':field,'left':a['state'][field],'right':b['state'][field]})
                    result['status']='IDENTICAL' if not result['differences'] else 'DIFFERENT'
                else:
                    def progress(index,total):
                        job['completed']=index
                        job['total']=total
                        job['current']='步骤 %d / %d' % (index+1,total)
                    runtime,result=run_scenario(image,scenario,progress)
                    job['_snapshot']=runtime.snapshot()
                    job['_firmware']=firmware
                    job['_scenario']=scenario
                job['completed']=job['total']
                job['result']=result
                job['status']='complete'
                (self.data_dir/'reports'/(job_id+'.json')).write_text(json.dumps(self.public_job(job_id),ensure_ascii=False,indent=2))
            except Exception as error:
                job['status']='error'
                job['error']=str(error)
        threading.Thread(target=run,daemon=True,name='mq80lab-job').start()
        return self.public_job(job_id)

    def public_job(self,key):
        if key not in self.jobs: raise ValueError('测试任务不存在。')
        return {k:v for k,v in self.jobs[key].items() if not k.startswith('_')}


def handler_type(lab,token):
    class Handler(BaseHTTPRequestHandler):
        server_version='MQ80Lab/1.1'

        def log_message(self,*args): pass

        def send(self,status,body,content_type='application/json; charset=utf-8'):
            data=json.dumps(body,ensure_ascii=False).encode() if not isinstance(body,bytes) else body
            self.send_response(status)
            self.send_header('Content-Type',content_type)
            self.send_header('Content-Length',str(len(data)))
            self.send_header('Cache-Control','no-store')
            self.send_header('X-Content-Type-Options','nosniff')
            self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'")
            self.end_headers()
            try:self.wfile.write(data)
            except (BrokenPipeError,ConnectionResetError):pass

        def allowed_host(self):
            return self.headers.get('Host')=='127.0.0.1:%d' % self.server.server_port

        def do_GET(self):
            if not self.allowed_host():return self.send(403,{'error':'Host 不允许。'})
            url=urlsplit(self.path)
            try:
                if url.path=='/api/bootstrap':
                    return self.send(200,{'token':token,'firmwares':lab.catalog(),'scenarios':BUILTINS,
                                         'layout':layout(lab.runtime.image),'coverage':COVERAGE,'version':'1.1.0'})
                if url.path=='/api/state':
                    with lab.lock:return self.send(200,lab.runtime.state())
                if url.path=='/api/report':
                    with lab.lock:return self.send(200,lab.runtime.report())
                if url.path=='/api/snapshot':
                    with lab.lock:return self.send(200,lab.runtime.snapshot())
                if url.path=='/api/recording':
                    with lab.lock:return self.send(200,lab.recording)
                if url.path=='/api/memory':
                    query=parse_qs(url.query)
                    address=int(query.get('address',['0x80b80'])[0],0)
                    size=int(query.get('size',['64'])[0])
                    with lab.lock:return self.send(200,lab.runtime.read_memory(address,size))
                if url.path.startswith('/api/jobs/'):
                    return self.send(200,lab.public_job(url.path.split('/')[-1]))
                static={'/':'index.html','/index.html':'index.html','/app.js':'app.js','/style.css':'style.css'}
                if url.path in static:
                    path=ROOT/'web'/static[url.path]
                    return self.send(200,path.read_bytes(),mimetypes.guess_type(path.name)[0] or 'application/octet-stream')
                self.send(404,{'error':'资源不存在。'})
            except Exception as error:self.send(400,{'error':str(error)})

        def do_POST(self):
            if not self.allowed_host():return self.send(403,{'error':'Host 不允许。'})
            expected='http://127.0.0.1:%d' % self.server.server_port
            if self.headers.get('Origin') not in [None,expected] or self.headers.get('X-Lab-Token')!=token:
                return self.send(403,{'error':'此操作需要本地应用会话。'})
            try:
                size=int(self.headers.get('Content-Length','0'))
                if not 0<size<=8*1024*1024:raise ValueError('请求大小须在 1 字节～8 MB 之间。')
                raw=self.rfile.read(size)
                if urlsplit(self.path).path=='/api/firmware':
                    if size>0x20000:raise ValueError('固件超过 128 KB。')
                    return self.send(200,lab.upload(raw,unquote(self.headers.get('X-Filename','firmware.bin'))))
                if self.headers.get('Content-Type','').split(';')[0]!='application/json':
                    raise ValueError('需要 application/json。')
                payload=json.loads(raw)
                if self.path=='/api/action':return self.send(200,lab.action(payload))
                if self.path=='/api/jobs':return self.send(200,lab.start_job(payload))
                self.send(404,{'error':'资源不存在。'})
            except Exception as error:
                state=None
                with lab.lock:
                    if lab.runtime.error_info:state=lab.runtime.state()
                self.send(400,{'error':str(error),'state':state})
    return Handler


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--port',type=int,default=0)
    parser.add_argument('--data-dir',type=Path,default=Path.home()/'Library/Application Support/MQ80 Firmware Lab')
    parser.add_argument('--parent-pid',type=int)
    args=parser.parse_args()
    lab=Lab(args.data_dir)
    server=ThreadingHTTPServer(('127.0.0.1',args.port),handler_type(lab,secrets.token_hex(32)))
    if args.parent_pid:
        def supervise():
            while True:
                time.sleep(2)
                try:os.kill(args.parent_pid,0)
                except OSError:server.shutdown();return
        threading.Thread(target=supervise,daemon=True).start()
    print(json.dumps({'ready':True,'url':'http://127.0.0.1:%d/' % server.server_port,'pid':os.getpid()}),flush=True)
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:server.server_close()


if __name__=='__main__':main()
