import argparse
import json
from pathlib import Path
from .profile import ASSETS,inspect_image
from .scenarios import BUILTINS,run_scenario


def main():
    parser=argparse.ArgumentParser(description='MQ80 离线场景测试；无设备访问')
    parser.add_argument('--firmware',type=Path,default=ASSETS/'battery-bar-v2.bin')
    parser.add_argument('--scenario',help='内置场景 ID 或场景 JSON 路径；省略则运行全部内置场景')
    parser.add_argument('--out',type=Path,default=Path('test-results/cli-report.json'))
    args=parser.parse_args()
    image=args.firmware.read_bytes()
    metadata=inspect_image(image,args.firmware.name)
    cases=BUILTINS
    if args.scenario:
        case=next((x for x in BUILTINS if x['id']==args.scenario),None)
        if case is None:case=json.loads(Path(args.scenario).read_text())
        cases=[case]
    rows=[]
    for case in cases:
        _,result=run_scenario(image,case)
        rows.append(result)
        print(result['status'],case.get('name','scenario'),result['error'] or '',flush=True)
    report={'format':'mq80lab-suite-v1','firmware':metadata,
            'status':'PASS' if all(row['status']=='PASS' for row in rows) else 'FAIL',
            'passed':sum(row['status']=='PASS' for row in rows),'total':len(rows),'rows':rows,'no_device_access':True}
    args.out.parent.mkdir(parents=True,exist_ok=True)
    args.out.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    if report['status']!='PASS':raise SystemExit(1)


if __name__=='__main__':main()
