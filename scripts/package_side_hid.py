"""Collect the offline-validated experimental firmware and host tool; no publish."""
import argparse
import hashlib
import json
import shutil
import zipfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build',type=Path,default=ROOT/'build/side-hid')
    parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args()
    build=args.build.resolve();out=args.out.resolve()
    manifest=json.loads((build/'build-manifest.json').read_text())
    validation=json.loads((build/'hid-validation.json').read_text())
    ota=json.loads((build/'ota-validation.json').read_text())
    image=build/manifest['modified_name']
    assert sha(image)==manifest['modified_sha256']==validation['modified_sha256']
    assert validation['status']=='PASS_OFFLINE_NOT_FLASHED'
    assert 'battery_regression' in validation['checks']
    assert ota['status']=='PASS_OFFLINE_ONLY' and ota['passed']==ota['count']==16
    assert any(row['candidate_sha256']==sha(image) for row in ota['rows'])
    archive=out.with_suffix('.zip')
    if (out.exists() and any(out.iterdir())) or archive.exists():
        raise SystemExit('Output already exists; use a new output directory')
    out.mkdir(parents=True,exist_ok=True)

    def copy(src,relative):
        destination=out/relative
        destination.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(src,destination)
    for name in ['DISCLAIMER.md','docs/SIDE_HID.md','simulator/requirements.txt']:
        copy(ROOT/name,name)
    for directory in ['controller','firmware/hid-source','simulator/mq80lab']:
        for src in (ROOT/directory).iterdir():
            if src.is_file() and src.suffix in ['.py','.c','.S','.ld','.md']:
                copy(src,src.relative_to(ROOT))
    for name in ['battery_bar.c','trampoline.S','validate_offline.py']:
        copy(ROOT/'firmware/source'/name,Path('firmware/source')/name)
    for name in ['stock.bin','battery-bar-v2.bin','layout.json','q101-captured-fn-layer.json']:
        copy(ROOT/'simulator/assets'/name,Path('simulator/assets')/name)
    evidence=['build-manifest.json','hid-validation.json','ota-validation.json',
              'receiver-read-only-probe.json','receiver-pattern-probe.json',
              'existing-suite-summary.json','reproducibility.json']
    for name in evidence:
        copy(build/name,Path('firmware/hid-validation')/name)
        copy(build/name,Path('build/side-hid')/name)
    copy(image,Path('build/side-hid')/image.name)
    copy(image,'firmware/experimental/side-hid.bin')
    for name in ['side_hid.disassembly.txt','side_hid.relocations.txt']:
        copy(build/name,Path('firmware/hid-validation')/name)
    copy(ROOT/'scripts/package_side_hid.py','scripts/package_side_hid.py')
    text=f'''# Q101 / MQ80 侧灯 HID 实验包

新增 USB 电脑控制侧灯，保留 Fn+B 电量条。新镜像已通过离线检查，尚未刷写实机。
当前只允许 USB 灯光控制；2.4G 完成通道探测和键盘端离线路径验证，可查询 INFO。

固件：`build/side-hid/{image.name}`

- codesize：{manifest['modified_size']}
- CRC：{manifest['modified_crc32_telink']}
- SHA-256：`{manifest['modified_sha256']}`

macOS 控制工具无第三方依赖。固件安装及 USB 模式就绪后，在本目录执行：

```sh
python3 controller/side_light.py info
python3 controller/side_light.py solid FF8000 --seconds 10 --brightness 50
python3 controller/side_light.py rainbow --seconds 10
python3 controller/side_light.py release
```

也可先离线运行，无需连接键盘：

```sh
python3 -m pip install -r simulator/requirements.txt
python3 controller/side_light.py --simulate rainbow --seconds 2
```

默认停止发包 2 秒后恢复原灯效；Fn+B 优先显示电量约 3 秒。工具先检查固件扩展标识，原电量条版和官方固件会被拒绝。控制工具没有刷写功能。

详细协议、构建命令和 2.4G 检查边界见 [说明](docs/SIDE_HID.md)。验证记录位于 `firmware/hid-validation/`，源码位于 `firmware/hid-source/` 和 `controller/`。

非官方固件，仅供个人学习交流。请阅读 [风险与免责声明](DISCLAIMER.md)。
'''
    (out/'README.md').write_text(text)
    checksums=''.join(sha(p)+'  '+str(p.relative_to(out))+'\n' for p in sorted(out.rglob('*')) if p.is_file())
    (out/'SHA256SUMS.txt').write_text(checksums)
    with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as z:
        for src in sorted(out.rglob('*')):
            if src.is_file():z.write(src,Path(out.name)/src.relative_to(out))
    with zipfile.ZipFile(archive) as z:
        assert z.testzip() is None
        assert all(not n.startswith('/') and '..' not in Path(n).parts for n in z.namelist())
    print(json.dumps({'directory':str(out),'zip':str(archive),'sha256':sha(archive),
                      'firmware_sha256':sha(image),'files':len([p for p in out.rglob('*') if p.is_file()])},ensure_ascii=False))


if __name__=='__main__':main()
