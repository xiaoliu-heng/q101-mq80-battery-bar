"""Generate Swift interoperability fixtures by executing the tested firmware."""
import json
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT),str(ROOT/'simulator')]
from controller.protocol import packet,decode,effect_payload
from dataclasses import asdict
from mq80lab.engine import Runtime


def main():
    rows=[]
    for version in ['3.1.0']:
        image=(ROOT/f'firmware/experimental/side-hid-rf-v{version}.bin').read_bytes()
        for path in ['direct','general','rf']:
            r=Runtime(image,{'connection':'rf' if path=='rf' else 'usb','cable':path!='rf'})
            operations=[(0,b'',None),(4,(2000).to_bytes(2,'little'),None),
                        (1,bytes(range(27)),None),(3,b'',None),(2,b'',None)]
            if version=='3.1.0':
                for index,effect in enumerate(['solid','blink','breathe','rainbow']):
                    params={'mode':effect,'brightness':25,'duration_seconds':3,'period_ms':1000}
                    color=bytes(3) if effect=='rainbow' else bytes.fromhex('34D399')
                    if effect!='rainbow':params['color']='34D399'
                    operations.append((5,effect_payload(effect,color,25,3000,0 if effect=='solid' else 1000,0x12345678+index),params))
            for sequence,(command,payload,params) in enumerate(operations,252):
                request=packet(command,sequence&255,payload)
                reply=r.receive_rf_hid(request) if path=='rf' else r.receive_raw_hid(request,path)
                rows.append({'path':path,'command':command,'sequence':sequence&255,'payload':list(payload),
                             'request':list(request),'reply':list(reply),'state':asdict(decode(request,reply)),
                             'lighting_input':params})
    output=ROOT/'macos/build/wire-fixtures.json';output.parent.mkdir(exist_ok=True)
    output.write_text(json.dumps(rows,indent=2)+'\n')
    print(json.dumps({'fixtures':len(rows),'real_firmware_execution':True,'hardware_access':False}))


if __name__=='__main__':main()
