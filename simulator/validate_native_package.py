"""Acceptance of the bundled native engine, including relocated builds. No UI/device I/O."""
import argparse
import base64
import hashlib
import json
import os
import select
import subprocess
import tempfile
import time
from pathlib import Path


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('engine',type=Path)
    parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args()
    result={'version':'2.4.1','engine':str(args.engine.resolve()),'transport':'stdio-json-lines',
            'no_device_access':True,'checks':[],'jobs':[]}
    with tempfile.TemporaryDirectory(prefix='mq80-native-acceptance-') as folder:
        process=subprocess.Popen([str(args.engine.resolve()),'--data-dir',folder],stdin=subprocess.PIPE,
                                 stdout=subprocess.PIPE,stderr=subprocess.PIPE,env={**os.environ,'PATH':'/usr/bin:/bin'})
        identity=0
        def call(method,params=None):
            nonlocal identity
            identity+=1
            process.stdin.write((json.dumps({'id':identity,'method':method,'params':params or {}})+'\n').encode());process.stdin.flush()
            if not select.select([process.stdout],[],[],30)[0]:raise TimeoutError(method)
            value=json.loads(process.stdout.readline())
            assert value['id']==identity and value['ok'],value
            return value['result']
        def check(name,condition):
            assert condition,name
            result['checks'].append({'name':name,'status':'PASS'})
        def job(params):
            value=call('start_job',params);deadline=time.monotonic()+90
            while value['status']=='running':
                if time.monotonic()>deadline:raise TimeoutError('job')
                time.sleep(.2);value=call('job',{'id':value['id']})
            assert value['status']=='complete',value
            result['jobs'].append(value)
            return value['result']
        try:
            boot=call('bootstrap');initial=call('state');image=initial['firmware']['id']
            stock=next(x['id'] for x in boot['firmwares'] if x['id']!=image)
            check('native bootstrap',boot['version']=='2.4.1' and len(boot['layout'])==84 and len(boot['scenarios'])==10 and len(boot['firmwares'])==6)
            check('Fn+B real pipeline',call('action',{'action':'query'})['battery']['query_active'])
            state=call('action',{'action':'preview','ms':3100})
            check('three-second expiry restores off',not state['battery']['query_active'] and state['rgb']['side']==[[0,0,0]]*9)
            call('action',{'action':'environment','values':{'main_off':False,'main_effect':1}})
            frames=[]
            for _ in range(20):
                state=call('action',{'action':'preview','ms':50})
                frames.append(hashlib.sha256(json.dumps(state['rgb']['main']).encode()).hexdigest())
            check('real main lighting animates',len(set(frames))>2 and any(any(x) for x in state['rgb']['main']))
            result['lighting_frame_hashes']=frames
            check('inferred physical mapping stays explicit',state['rgb']['physical_main_mapping_verified'] is False)
            call('action',{'action':'sleep'})
            saved=call('snapshot')
            a=call('action',{'action':'tap','keys':['3,1'],'hold_ms':50})
            check('first key after PM wake',a['host_text']=='a' and a['hid']=='0000000000000000')
            call('action',{'action':'restore','snapshot':saved})
            b=call('action',{'action':'tap','keys':['3,1'],'hold_ms':50})
            check('sleeping snapshot restores caller and report',a['reports']==b['reports'] and b['host_text']=='a')
            check('memory reads real HID',call('memory',{'address':'0x80b80','size':8})['bytes']==b['hid'])
            check('recording export',len(call('recording')['steps'])==1)
            report=call('report');check('report declares offline scope',report['no_device_access'] and bool(report['limitations']))
            suite=job({'kind':'suite','firmware':image})
            check('10 built-in scenarios',suite['status']=='PASS' and suite['passed']==suite['total']==10)
            comparison=job({'kind':'compare','firmware':image,'other':stock,'scenario':boot['scenarios'][0]})
            check('stock/v2 first-key comparison',comparison['left']['status']=='PASS' and comparison['right']['status']=='PASS' and comparison['status']=='IDENTICAL')
            for case in ['valid_image','incomplete','crc_error','header_readback_error']:
                ota=job({'kind':'ota','firmware':stock,'other':image,'ota_case':case})
                check('virtual OTA '+case,ota['status']=='PASS' and ota['no_device_access'])
            side=next(x['id'] for x in boot['firmwares'] if x['side_hid'])
            call('action',{'action':'reset','firmware':side,'environment':{'connection':'usb','cable':True}})
            state=call('action',{'action':'side_hid','mode':'solid','colors':[[255,0,0]]*9})
            check('bundled side HID codec and firmware',state['rgb']['side']==[[162,0,0]]*9)
            call('action',{'action':'side_hid','mode':'rainbow'})
            state=call('action',{'action':'preview','ms':3100})
            check('packaged rainbow sends real USB frames',state['side_hid']['active'] and state['side_hid']['frames_sent']>60)
            state=call('action',{'action':'side_hid','mode':'release'})
            check('packaged HID release restores side off',not state['side_hid']['active'] and state['rgb']['side']==[[0,0,0]]*9)
            rf=next(x['id'] for x in boot['firmwares'] if x['firmware_version']=='3.0.1')
            call('action',{'action':'reset','firmware':rf,'environment':{'connection':'rf','cable':False}})
            state=call('action',{'action':'side_hid','mode':'solid','colors':[[0,0,255]]*9})
            check('bundled RF control executes real receive and closing ACKs',state['rgb']['side']==[[0,0,162]]*9 and state['stats']['calls']['rf_configuration_closing_ack']==6)
            report=call('report')
            reply=bytes.fromhex(next(x['details']['reply'] for x in report['trace'] if x['kind']=='raw-hid'))
            check('packaged firmware emits version and build in real INFO reply',reply[23:]==b'FV\x01\x03\x00\x01\x0f\x02\x00' and state['firmware']['firmware_version']=='3.0.1' and state['firmware']['firmware_build']==2)
            call('action',{'action':'side_hid','mode':'rainbow'})
            state=call('action',{'action':'preview','ms':3100})
            check('packaged wireless rainbow ten FPS',state['side_hid']['active'] and state['side_hid']['fps']==10 and state['side_hid']['frames_sent']==33)
            call('action',{'action':'query'})
            state=call('state')
            check('wireless Fn+B overrides active control',state['battery']['query_active'] and state['side_hid']['active'])
            call('action',{'action':'side_hid','mode':'stop'})
            call('action',{'action':'preview','ms':3100})
            state=call('action',{'action':'sleep'})
            check('wireless timeout permits original sleep',state['status']=='sleeping' and not state['side_hid']['active'])
            state=call('action',{'action':'tap','keys':['3,1'],'hold_ms':50})
            check('wireless lighting preserves first key after wake',state['host_text']=='a' and state['stats']['flash_operations']==0)
            result['status']='PASS'
        finally:
            process.stdin.close();process.wait(timeout=10)
            result['engine_exit']=process.returncode
            result['stderr']=process.stderr.read().decode()
            args.out.parent.mkdir(parents=True,exist_ok=True)
            args.out.write_text(json.dumps(result,ensure_ascii=False,indent=2))
        assert process.returncode==0,result['stderr']
    print(json.dumps({'status':result['status'],'checks':len(result['checks']),'out':str(args.out)},ensure_ascii=False))


if __name__=='__main__':main()
