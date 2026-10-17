"""End-to-end native app/HTTP/CLI checks in an isolated explicit demo session."""
import argparse
import concurrent.futures
import hashlib
import json
import os
import plistlib
import select
import shutil
import socket
import stat
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--app',type=Path,required=True)
    p.add_argument('--out',type=Path,required=True)
    args=p.parse_args();app=args.app.resolve();rows=[]
    app_version=plistlib.loads((app/'Contents/Info.plist').read_bytes())['CFBundleShortVersionString']
    def check(name,value):
        assert value,name
        rows.append({'name':name,'status':'PASS'})
    with tempfile.TemporaryDirectory(prefix='kryra-native-e2e-') as folder:
        env=dict(os.environ,KRYRA_HOME=folder)
        process=subprocess.Popen([str(app/'Contents/MacOS/Kryra'),'--headless','--demo'],
                                 env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        try:
            assert select.select([process.stdout],[],[],15)[0],'app startup timeout'
            ready=json.loads(process.stdout.readline());check('headless explicit demo starts',ready['ready'] and ready['demo'])
            config_path=Path(folder)/'api.json';config=json.loads(config_path.read_text())
            port=config['port'];base='http://127.0.0.1:'+str(port)
            check('credential file is private',stat.S_IMODE(config_path.stat().st_mode)==0o600)
            check('state directory is private',stat.S_IMODE(Path(folder).stat().st_mode)==0o700)
            check('token has 256 random bits',len(config['token'])==64)
            opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
            def request(path,body=None,auth=True,headers=None):
                h={'Authorization':'Bearer '+config['token']} if auth else {}
                if headers:h.update(headers)
                data=None if body is None else json.dumps(body).encode()
                if data is not None:h['Content-Type']='application/json'
                q=urllib.request.Request(base+path,data=data,headers=h)
                try:
                    with opener.open(q,timeout=8) as r:return r.status,json.load(r)
                except urllib.error.HTTPError as e:return e.code,json.load(e)
            cli=app/'Contents/Resources/bin/kryractl'
            def command(*values,expected=0):
                r=subprocess.run([str(cli),*values],env=env,check=False,capture_output=True,text=True,timeout=10)
                assert r.returncode==expected,(values,r.stderr)
                return json.loads(r.stdout if expected==0 else r.stderr)
            check('unauthenticated request refused',request('/v1/status',auth=False)[0]==401)
            check('wrong token refused',request('/v1/status',headers={'Authorization':'Bearer wrong'})[0]==401)
            check('browser Origin refused',request('/v1/status',headers={'Origin':'https://example.org'})[0]==401)
            check('foreign Host refused',request('/v1/status',headers={'Host':'example.org'})[0]==401)
            check('API health',request('/v1/health')[1]['demo'] is True)
            check('API version matches bundle',request('/v1/health')[1]['app_version']==app_version)
            duplicate=subprocess.run([str(app/'Contents/MacOS/Kryra'),'--headless','--demo'],env=env,capture_output=True,text=True,timeout=8)
            check('duplicate instance leaves active API untouched',duplicate.returncode==1 and json.loads(config_path.read_text())['token']==config['token'])
            check('unknown route',request('/missing')[0]==404)
            for _ in range(30):
                state=command('status')
                if state['device']['connected']:break
                time.sleep(.1)
            check('native CLI battery',command('battery')['percent']==88)
            check('native CLI active refresh',command('battery','--refresh')['stale'] is False)
            state=command('solid','0080FF','--brightness','50')
            check('CLI solid reaches shared controller',state['lighting']['mode']=='solid' and state['lighting']['brightness']==50)
            state=request('/v1/status')[1]
            check('API and CLI observe same state',state['lighting']['mode']=='solid')
            state=command('solid','123456')
            check('CLI default is a dim five-second notification',state['lighting']['brightness']==25 and state['lighting']['duration_seconds']==5 and state['lighting']['continuous'] is False)
            check('CLI refuses unlimited notifications','error' in command('solid','123456','--continuous',expected=1))
            check('continuous and duration cannot be combined','error' in command('rainbow','--continuous','--seconds','3',expected=1))
            check('API refuses nonboolean continuous option',request('/v1/lighting',{'mode':'rainbow','continuous':1})[0]==400)
            check('bad brightness rejected',request('/v1/lighting',{'mode':'solid','color':'FFFFFF','brightness':101})[0]==400)
            check('invalid request leaves current effect',request('/v1/status')[1]['lighting']['mode']=='solid')
            check('CLI validates before contacting API','error' in command('solid','bad',expected=1))
            state=command('rainbow','--period','1000','--seconds','0.7')
            check('native CLI rainbow',state['lighting']['mode']=='rainbow')
            time.sleep(.35)
            playback=command('status')['lighting']
            check('rainbow runs in firmware without further commands',playback['commands_sent']==1 and playback['frames_sent']==1 and playback['execution']=='firmware')
            time.sleep(.6)
            check('finite effect restores original',command('status')['lighting']['mode']=='original')
            for effect in ['blink','breathe']:
                state=command(effect,'34D399','--period','1000','--seconds','0.3')
                check(effect+' CLI uses firmware playback',state['lighting']['mode']==effect and state['lighting']['execution']=='firmware' and state['lighting']['commands_sent']==1)
                time.sleep(.45)
                check(effect+' ends independently',command('status')['lighting']['mode']=='original')
            check('excessive duration is refused',request('/v1/lighting',{'mode':'rainbow','duration_seconds':60.01})[0]==400)
            duplicate_body={'mode':'breathe','color':'34D399','duration_seconds':0.8,'request_id':73}
            first=request('/v1/lighting',duplicate_body)[1]['lighting']
            time.sleep(.25)
            again=request('/v1/lighting',duplicate_body)[1]['lighting']
            check('HTTP retry ID preserves the original firmware deadline',first['request_id']==again['request_id']==73 and again['remaining_ms']<first['remaining_ms']-150)
            time.sleep(.7)
            check('replaying an expired ID does not restart',request('/v1/lighting',duplicate_body)[1]['lighting']['active'] is False)
            state=command('blink','FFAA33','--seconds','0.3','--request-id','4294967295')
            check('CLI preserves the full unsigned request ID',state['lighting']['request_id']==4294967295)
            check('zero CLI request ID is rejected','error' in command('rainbow','--request-id','0',expected=1))
            colors=['FF0000','FF8000','FFFF00','80FF00','00FF00','00FF80','00FFFF','0080FF','8000FF']
            state=command('frame',*colors)
            check('nine independent colors',state['lighting']['colors']==colors)
            check('CLI release',command('release')['lighting']['mode']=='original')
            # Read-only callers and color callers share the worker; state remains
            # valid and the explicit final command must win after concurrency.
            def concurrent_request(i):
                return request('/v1/lighting',{'mode':'solid','color':f'{i:06X}'}) if i%2 else request('/v1/status')
            with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
                results=list(pool.map(concurrent_request,range(12)))
            check('concurrent requests are bounded',all(code in [200,409] for code,_ in results))
            check('final command wins after concurrent callers',command('solid','34D399')['lighting']['colors']==['34D399']*9)
            listing=subprocess.run(['lsof','-nP','-a','-p',str(process.pid),'-iTCP','-sTCP:LISTEN'],capture_output=True,text=True,check=True).stdout
            check('listener binds only IPv4 loopback',f'127.0.0.1:{port}' in listing and f'*:{port}' not in listing and f'[::]:{port}' not in listing)
            def raw(value):
                with socket.create_connection(('127.0.0.1',port),timeout=4) as s:
                    s.sendall(value);s.shutdown(socket.SHUT_WR)
                    result=b''
                    while True:
                        chunk=s.recv(8192)
                        if not chunk:break
                        result+=chunk
                    return result.split(b'\r\n',1)[0]
            check('oversized body rejected',b'413' in raw(b'POST /v1/lighting HTTP/1.1\r\nHost: 127.0.0.1\r\nContent-Length: 99999\r\n\r\n'))
            check('ambiguous framing rejected',b'400' in raw(b'POST / HTTP/1.1\r\nContent-Length: 0\r\nContent-Length: 0\r\n\r\n'))
            # A copied skill helper works without the app source tree or PATH.
            copied=Path(folder)/'isolated-skill/scripts/kryractl';copied.parent.mkdir(parents=True)
            shutil.copy2(app/'Contents/Resources/Skill/kryra/scripts/kryractl',copied)
            isolated_env=dict(env,PATH='/usr/bin:/bin');isolated_env.pop('PYTHONPATH',None)
            result=subprocess.run([str(copied),'battery'],env=isolated_env,cwd=folder,capture_output=True,text=True,check=True)
            check('copied skill helper needs no repo or Python',json.loads(result.stdout)['percent']==88)
            result=subprocess.run([str(copied),'breathe','34D399','--seconds','0.3'],env=isolated_env,cwd=folder,capture_output=True,text=True,check=True)
            check('installed skill helper sends a native notification',json.loads(result.stdout)['lighting']['execution']=='firmware')
            old_token=config['token'];process.terminate();process.wait(timeout=8)
            check('graceful quit',process.returncode==0 and not config_path.exists())
            process=subprocess.Popen([str(app/'Contents/MacOS/Kryra'),'--headless','--demo'],env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
            assert select.select([process.stdout],[],[],15)[0];json.loads(process.stdout.readline())
            new_config=json.loads(config_path.read_text())
            check('API token rotates on restart',new_config['token']!=old_token)
        finally:
            if process.poll() is None:
                process.terminate()
                try:process.wait(timeout=8)
                except subprocess.TimeoutExpired:process.kill();process.wait()
            error=process.stderr.read()
            assert process.returncode==0,error
    result={'status':'PASS','app_version':app_version,'checks':rows,'count':len(rows),'mode':'explicit-demo',
            'physical_hid_access':False,'tested':'native built app, real loopback listener and native CLI',
            'app_executable_sha256':hashlib.sha256((app/'Contents/MacOS/Kryra').read_bytes()).hexdigest(),
            'cli_sha256':hashlib.sha256(cli.read_bytes()).hexdigest()}
    args.out.parent.mkdir(exist_ok=True,parents=True);args.out.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'status':'PASS','checks':len(rows),'physical_hid_access':False}))


if __name__=='__main__':main()
