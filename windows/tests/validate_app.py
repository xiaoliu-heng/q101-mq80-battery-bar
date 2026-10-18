"""Launch the actual native app and CLI with an isolated demo profile."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import time
import urllib.error
import urllib.request

parser=argparse.ArgumentParser()
parser.add_argument('--app',type=Path,required=True)
parser.add_argument('--cli',type=Path,required=True)
parser.add_argument('--out',type=Path,required=True)
parser.add_argument('--skill-binding',type=Path,help='Use an installed Skill binding and its isolated runtime home.')
args=parser.parse_args()
out=args.out.resolve();out.parent.mkdir(parents=True,exist_ok=True)
home=out.parent/('e2e-profile-'+str(time.time_ns()))
if args.skill_binding:
    binding=json.loads(args.skill_binding.read_text(encoding='utf-8'))
    args.cli=Path(binding['cli_path']);home=Path(binding['runtime_home'])
env=dict(os.environ,KRYRA_HOME=str(home))
process=None
checks=[]

def start():
    global process
    process=subprocess.Popen([str(args.app.resolve()),'--demo','--headless','--home',str(home)],env=env,
                             stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    deadline=time.monotonic()+10
    while time.monotonic()<deadline:
        if process.poll() is not None:raise RuntimeError('Native application exited before API startup')
        try:return json.loads((home/'api.json').read_text(encoding='utf-8'))
        except (OSError,json.JSONDecodeError):time.sleep(.05)
    raise RuntimeError('API startup timed out')

def cli(*arguments,ok=True):
    result=subprocess.run([str(args.cli.resolve()),*arguments],env=env,text=True,encoding='utf-8',capture_output=True,timeout=12)
    assert (result.returncode==0)==ok,(arguments,result.stdout,result.stderr)
    return json.loads(result.stdout or result.stderr)

def check(name,condition):
    assert condition,name
    checks.append({'test':name,'pass':True})

try:
    config=start()
    if args.skill_binding:check('installed Skill binding resolves the packaged native CLI',args.cli.resolve()==(args.app.resolve().parent/'kryractl.exe'))
    # The API becomes available before the first background battery read completes.
    ready_deadline=time.monotonic()+5
    while True:
        s=cli('status')
        if s['connected'] or time.monotonic()>=ready_deadline:break
        time.sleep(.05)
    check('native demo app and native CLI use same controller',s['demo'] is True and s['connected'])
    check('battery query is explicit demo data',cli('battery')['percent']==88)
    baseline=s['diagnostics']['hid_requests_sent']
    check('poll interval configurable',cli('settings','--poll-interval','120')['battery_poll_interval_seconds']==120)
    check('setting change sends no HID',cli('status')['diagnostics']['hid_requests_sent']==baseline)
    cli('settings','--poll-interval','0',ok=False)
    for mode in ['solid','blink','breathe','rainbow']:
        arguments=[mode]+([] if mode=='rainbow' else ['34D399'])+['--brightness','25','--seconds','0.2']
        state=cli(*arguments)
        check(mode+' uses one firmware command',state['lighting']['commands_sent']==1 and state['lighting']['execution']=='firmware_local')
        count=state['diagnostics']['hid_requests_sent'];time.sleep(.35)
        ended=cli('status');check(mode+' expires without host traffic',not ended['lighting']['active'] and ended['diagnostics']['hid_requests_sent']==count)
    cli('solid','GGGGGG',ok=False)
    cli('rainbow','--seconds','61',ok=False)
    cli('blink','34D399','--seconds','2');check('explicit release restores lighting',not cli('release')['lighting']['active'])
    cli('quit');process.wait(timeout=10)
    check('normal exit removes own API credentials',not (home/'api.json').exists())
    original_token=config['token'];config=start()
    check('restart preserves settings',cli('settings')['battery_poll_interval_seconds']==120)
    check('restart rotates credentials',config['token']!=original_token)
    cli('quit');process.wait(timeout=10)
    report={'demo':True,'real_device_access':False,'passed':len(checks),'failed':0,'checks':checks}
except Exception as error:
    report={'demo':True,'passed':len(checks),'failed':1,'error':str(error),'checks':checks}
finally:
    if process and process.poll() is None:
        try:cli('quit');process.wait(timeout=10)
        except Exception:process.terminate();process.wait(timeout=5)
out.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print(json.dumps({'passed':report['passed'],'failed':report['failed'],'report':str(out)},ensure_ascii=False))
raise SystemExit(1 if report['failed'] else 0)
