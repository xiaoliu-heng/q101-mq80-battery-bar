"""Build a native SwiftUI/AppKit app with a bundled stdio firmware engine."""
import json
import os
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parent
BUILD=ROOT/'build'
DIST=ROOT/'dist'
APP=DIST/'MQ80 Firmware Lab 2.4.1.app'


def run(*args):
    print(' '.join(map(str,args)),flush=True)
    subprocess.run(list(map(str,args)),cwd=ROOT,check=True)


def main():
    BUILD.mkdir(exist_ok=True)
    DIST.mkdir(exist_ok=True)
    run(sys.executable,'-m','PyInstaller','--noconfirm','--onedir','--name','MQ80LabEngine',
        '--distpath',BUILD/'engine-dist','--workpath',BUILD/'pyinstaller-native','--specpath',BUILD,
        '--collect-binaries','unicorn','--add-data',str(ROOT/'assets')+':assets',
        '--paths',ROOT.parent,'--add-data',str(ROOT.parent/'firmware/experimental/side-hid.bin')+':assets',
        '--add-data',str(ROOT.parent/'firmware/experimental/side-hid-rf.bin')+':assets',
        '--add-data',str(ROOT.parent/'firmware/experimental/side-hid-rf-v3.0.0.bin')+':assets',
        '--add-data',str(ROOT.parent/'firmware/experimental/side-hid-rf-v3.0.1.bin')+':assets',
        ROOT/'run_native.py')
    contents=APP/'Contents'
    if contents.exists():shutil.rmtree(contents)
    (contents/'MacOS').mkdir(parents=True,exist_ok=True)
    (contents/'Resources').mkdir(parents=True,exist_ok=True)
    engine=contents/'Resources/engine'
    shutil.copytree(BUILD/'engine-dist/MQ80LabEngine',engine,symlinks=True)
    sources=[ROOT/'native'/name for name in ['App.swift','Models.swift','Engine.swift','Keyboard.swift','PhysicalInput.swift','Views.swift','LabsViews.swift','SideHIDView.swift']]
    run('xcrun','swiftc','-O','-parse-as-library','-target','arm64-apple-macosx13.0',*sources,
        '-framework','AppKit','-framework','SwiftUI','-o',contents/'MacOS/MQ80FirmwareLab')
    run('xcrun','swiftc','native/Icon.swift','-framework','AppKit','-o',BUILD/'make-icon')
    run(BUILD/'make-icon',BUILD/'AppIcon.iconset')
    run('iconutil','-c','icns',BUILD/'AppIcon.iconset','-o',contents/'Resources/AppIcon.icns')
    info={'CFBundleExecutable':'MQ80FirmwareLab','CFBundleIdentifier':'local.xiaoliu.mq80-firmware-lab.native',
          'CFBundleName':'MQ80 Firmware Lab','CFBundleDisplayName':'MQ80 Firmware Lab',
          'CFBundlePackageType':'APPL','CFBundleVersion':'8','CFBundleShortVersionString':'2.4.1',
          'CFBundleIconFile':'AppIcon','LSMinimumSystemVersion':'13.0','NSHighResolutionCapable':True}
    (contents/'Info.plist').write_bytes(plistlib.dumps(info))
    shutil.copyfile(ROOT/'THIRD_PARTY_NOTICES.txt',contents/'Resources/THIRD_PARTY_NOTICES.txt')
    shutil.copyfile(ROOT/'使用说明.txt',contents/'Resources/使用说明.txt')
    run('codesign','--force','--deep','--sign','-',APP)
    run('codesign','--verify','--deep','--strict','--verbose=2',APP)
    print(json.dumps({'app':str(APP),'architecture':'arm64','signing':'local ad-hoc','hardware_access':False},indent=2),flush=True)


if __name__=='__main__':main()
