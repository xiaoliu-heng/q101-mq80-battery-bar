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
    current_version=json.loads((ROOT/'firmware/hid-source/version.json').read_text())['version']
    parser.add_argument('--build',type=Path,default=ROOT/'build'/('side-hid-rf-v'+current_version))
    parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args()
    build=args.build.resolve();out=args.out.resolve()
    manifest=json.loads((build/'build-manifest.json').read_text())
    version=manifest['firmware_version']
    build_relative=Path('build')/('side-hid-rf-v'+version)
    evidence_relative=Path('firmware')/('v'+version+'-validation')
    validation=json.loads((build/'hid-validation.json').read_text())
    ota=json.loads((build/'ota-validation.json').read_text())
    image=build/manifest['modified_name']
    assert sha(image)==manifest['modified_sha256']==validation['modified_sha256']
    assert validation['status']=='PASS_OFFLINE_NOT_FLASHED'
    assert 'battery_regression' in validation['checks']
    assert ota['status']=='PASS_OFFLINE_ONLY' and ota['passed']==ota['count']==32
    assert any(row['candidate_sha256']==sha(image) for row in ota['rows'])
    for name in ['preflash-core.json','preflash-soak.json','preflash-ota.json']:
        report=json.loads((build/name).read_text())
        assert report['status']=='PASS_OFFLINE_ONLY_NOT_FLASHED'
        assert report['firmware_sha256']==sha(image)
    archive=out.parent/(out.name+'.zip')
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
            if src.is_file() and src.suffix in ['.py','.c','.S','.ld','.md','.json']:
                copy(src,src.relative_to(ROOT))
    for name in ['battery_bar.c','trampoline.S','validate_offline.py','validate_extended.py','validate_patch_rv32.py']:
        copy(ROOT/'firmware/source'/name,Path('firmware/source')/name)
    for name in ['stock.bin','battery-bar-v2.bin','layout.json','q101-captured-fn-layer.json']:
        copy(ROOT/'simulator/assets'/name,Path('simulator/assets')/name)
    evidence=['build-manifest.json','hid-validation.json','ota-validation.json',
              'existing-suite-summary.json','reproducibility.json','preflash-core.json',
              'preflash-soak.json','preflash-ota.json','independent-battery.json','preflash-summary.json']
    for name in evidence:
        copy(build/name,evidence_relative/name)
        copy(build/name,build_relative/name)
    copy(image,build_relative/image.name)
    copy(image,'firmware/experimental/side-hid-rf-v'+version+'.bin')
    copy(ROOT/'firmware/experimental/side-hid-rf.bin','firmware/experimental/side-hid-rf.bin')
    copy(ROOT/'firmware/experimental/side-hid.bin','firmware/experimental/side-hid.bin')
    copy(ROOT/'firmware/experimental/side-hid-rf-v3.0.0.bin','firmware/experimental/side-hid-rf-v3.0.0.bin')
    for name in ['receiver-read-only-probe.json','receiver-pattern-probe.json']:
        copy(ROOT/'firmware/hid-validation'/name,Path('firmware/hid-validation')/name)
    for name in ['side_hid.disassembly.txt','side_hid.relocations.txt']:
        copy(build/name,evidence_relative/name)
    copy(ROOT/'scripts/package_side_hid.py','scripts/package_side_hid.py')
    text=f'''# Q101 / MQ80 侧灯 HID v{version} 实验包

支持 USB / 2.4G 电脑控制侧灯，保留 Fn+B 电量条。新镜像已通过离线检查，尚未刷写实机或验证真实无线灯效。
USB 彩虹默认 20 FPS，2.4G 默认 10 FPS。无线控制有效期间暂缓休眠，释放或停发超时后恢复原计时。

固件：`{build_relative}/{image.name}`

- 固件版本：{version}，构建号 {manifest['firmware_build']}，可通过 INFO 查询
- codesize：{manifest['modified_size']}
- CRC：{manifest['modified_crc32_telink']}
- SHA-256：`{manifest['modified_sha256']}`

macOS 控制工具无第三方依赖。固件安装及 USB 模式就绪后，在本目录执行：

```sh
python3 controller/side_light.py info
python3 controller/side_light.py solid FF8000 --seconds 10 --brightness 50
python3 controller/side_light.py rainbow --seconds 10
python3 controller/side_light.py release

# 键盘切到 2.4G，已休眠时先按键唤醒
python3 controller/side_light.py --receiver info
python3 controller/side_light.py --receiver rainbow --seconds 10
```

也可先离线运行，无需连接键盘：

```sh
python3 -m pip install -r simulator/requirements.txt
python3 controller/side_light.py --simulate rainbow --seconds 2
python3 controller/side_light.py --simulate --receiver rainbow --seconds 2
```

默认停止发包 2 秒后恢复原灯效；Fn+B 优先显示电量约 3 秒。工具先检查固件扩展标识，原电量条版和官方固件会被拒绝。控制工具没有刷写功能。

详细协议、构建命令和 2.4G 检查边界见 [说明](docs/SIDE_HID.md)。新版验证记录位于 `{evidence_relative}/`，`firmware/hid-validation/` 的接收器探测为此前旧固件的历史记录。源码位于 `firmware/hid-source/` 和 `controller/`。`firmware/hid-source/version.json` 为后续版本号和构建号的唯一配置源。

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
