"""Build the native Kryra menu-bar app and CLI; no hardware access or launch."""
import argparse
import json
import plistlib
import shutil
import subprocess
from pathlib import Path

ROOT=Path(__file__).resolve().parent
BUILD=ROOT/'build'
DIST=ROOT.parent/'dist'


def run(*args):
    subprocess.run([str(x) for x in args],check=True,cwd=ROOT)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--universal',action='store_true')
    args=parser.parse_args()
    BUILD.mkdir(exist_ok=True);DIST.mkdir(exist_ok=True)
    app=DIST/'Kryra.app';contents=app/'Contents'
    if contents.exists():shutil.rmtree(contents)
    (contents/'MacOS').mkdir(parents=True)
    resources=contents/'Resources';resources.mkdir()
    architectures=['arm64','x86_64'] if args.universal else ['arm64']
    app_sources=sorted((ROOT/'Kryra').glob('*.swift'))
    cli_sources=[ROOT/'Kryra/Protocol.swift',ROOT/'Kryra/RuntimeFiles.swift',ROOT/'kryractl/Main.swift']
    for name,sources in [('Kryra',app_sources),('kryractl',cli_sources)]:
        outputs=[]
        for arch in architectures:
            output=BUILD/(name+'-'+arch);outputs.append(output)
            run('xcrun','swiftc','-swift-version','5','-warnings-as-errors','-O','-parse-as-library',
                '-target',arch+'-apple-macosx13.0',*sources,'-framework','AppKit','-framework','SwiftUI',
                '-framework','IOKit','-framework','Network','-framework','Security','-framework','ServiceManagement','-o',output)
        destination=contents/'MacOS/Kryra' if name=='Kryra' else resources/'bin/kryractl'
        destination.parent.mkdir(exist_ok=True)
        if len(outputs)==1:shutil.copy2(outputs[0],destination)
        else:run('lipo','-create',*outputs,'-output',destination)
    skill=resources/'Skill/kryra'
    shutil.copytree(ROOT/'skill/kryra',skill)
    (skill/'scripts').mkdir(exist_ok=True)
    shutil.copy2(resources/'bin/kryractl',skill/'scripts/kryractl')
    run('codesign','--force','--sign','-',resources/'bin/kryractl')
    run('codesign','--force','--sign','-',skill/'scripts/kryractl')
    shutil.copy2(ROOT/'API.html',resources/'API.html')
    run('xcrun','swiftc',ROOT/'Icon.swift','-framework','AppKit','-o',BUILD/'make-icon')
    run(BUILD/'make-icon',BUILD/'Kryra.iconset')
    run('iconutil','-c','icns',BUILD/'Kryra.iconset','-o',resources/'Kryra.icns')
    info={'CFBundleExecutable':'Kryra','CFBundleIdentifier':'local.xiaoliu.kryra',
          'CFBundleName':'Kryra','CFBundleDisplayName':'Kryra','CFBundlePackageType':'APPL',
          'CFBundleVersion':'3','CFBundleShortVersionString':'1.1.0','LSMinimumSystemVersion':'13.0',
          'LSUIElement':True,'NSHighResolutionCapable':True,'NSSupportsAutomaticTermination':False,'CFBundleIconFile':'Kryra',
          'NSHumanReadableCopyright':'Kryra · Q101 / MQ80 community project'}
    (contents/'Info.plist').write_bytes(plistlib.dumps(info))
    run('codesign','--force','--deep','--sign','-',app)
    run('codesign','--verify','--deep','--strict','--verbose=2',app)
    print(json.dumps({'app':str(app),'architectures':architectures,'version':info['CFBundleShortVersionString'],'signing':'local ad-hoc'},ensure_ascii=False))


if __name__=='__main__':main()
