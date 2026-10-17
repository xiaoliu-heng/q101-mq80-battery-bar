"""Create local ZIP / DMG artifacts for the native app. Does not publish."""
import argparse
import hashlib
import json
import plistlib
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def run(*args):return subprocess.run([str(x) for x in args],check=True,capture_output=True,text=True).stdout
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--app',type=Path,default=ROOT/'dist/Kryra.app')
    args=parser.parse_args();app=args.app.resolve();dist=ROOT/'dist'
    info=plistlib.loads((app/'Contents/Info.plist').read_bytes())
    assert info['CFBundleIdentifier']=='local.xiaoliu.kryra' and info['LSUIElement']
    assert 'LSEnvironment' not in info,'Do not package a demo/QA app'
    run('codesign','--verify','--deep','--strict',app)
    binary=app/'Contents/MacOS/Kryra';cli=app/'Contents/Resources/bin/kryractl'
    e2e=json.loads((ROOT/'test-results/kryra-app-e2e.json').read_text())
    core=json.loads((ROOT/'test-results/kryra-core-tests.json').read_text())
    assert e2e['status']==core['status']=='PASS'
    assert e2e['app_executable_sha256']==sha(binary) and e2e['cli_sha256']==sha(cli),'Test the final app before packaging'
    assert set(run('lipo','-archs',binary).split())=={'arm64','x86_64'}
    assert set(run('lipo','-archs',cli).split())=={'arm64','x86_64'}
    assert run(cli,'--version').strip()==info['CFBundleShortVersionString']
    stem='Kryra-'+info['CFBundleShortVersionString']+'-universal'
    archive=dist/(stem+'.zip');dmg=dist/(stem+'.dmg')
    assert not archive.exists() and not dmg.exists(),'Use new artifact names instead of replacing a delivered archive'
    run('ditto','-c','-k','--sequesterRsrc','--keepParent',app,archive)
    with zipfile.ZipFile(archive) as z:
        assert z.testzip() is None
        assert all(not n.startswith('/') and '..' not in Path(n).parts for n in z.namelist())
    staging_root=ROOT/'.release';staging_root.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='kryra-native-',dir=staging_root) as temporary:
        staging=Path(temporary)
        shutil.copytree(app,staging/app.name,symlinks=True)
        (staging/'Applications').symlink_to('/Applications',target_is_directory=True)
        run('hdiutil','create','-volname','Kryra','-srcfolder',staging,'-format','UDZO',dmg)
    run('hdiutil','verify',dmg)
    artifacts=[{'path':str(p.relative_to(ROOT)),'size':p.stat().st_size,'sha256':sha(p)} for p in [archive,dmg]]
    result={'status':'PASS','version':info['CFBundleShortVersionString'],'architectures':['arm64','x86_64'],
            'app_executable_sha256':sha(binary),'cli_sha256':sha(cli),'artifacts':artifacts,
            'signing':'local ad-hoc; not Developer ID notarized','published':False}
    (dist/(stem+'-manifest.json')).write_text(json.dumps(result,indent=2)+'\n')
    (dist/(stem+'-SHA256SUMS.txt')).write_text(''.join(p['sha256']+'  '+Path(p['path']).name+'\n' for p in artifacts))
    print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':main()
