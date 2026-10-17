"""Build and package Kryra Windows with a local .NET 8 SDK; no system install."""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import zipfile
import zlib
import xml.etree.ElementTree as ET

ROOT=Path(__file__).resolve().parents[1]
VERSION=ET.parse(ROOT/'windows/Directory.Build.props').findtext('.//Version')

def icon():
    # Rasterize the app's own vector waveform for the Win32 executable resource.
    points=[(5,16),(9,16),(11,8),(14,25),(17,5),(20,25),(23,12),(25,18),(28,16)]
    def distance(x,y,a,b):
        dx,dy=b[0]-a[0],b[1]-a[1]
        t=max(0,min(1,((x-a[0])*dx+(y-a[1])*dy)/(dx*dx+dy*dy)))
        return math.hypot(x-a[0]-t*dx,y-a[1]-t*dy)
    def png(size):
        rows=[]
        for y in range(size):
            row=bytearray([0])
            for x in range(size):
                px,py=(x+.5)*32/size,(y+.5)*32/size
                edge=math.hypot(max(abs(px-16)-9,0),max(abs(py-16)-9,0))-6
                alpha=max(0,min(1,.5-edge*size/32))
                wave=min(distance(px,py,a,b) for a,b in zip(points,points[1:]))
                blend=max(0,min(1,(1.2-wave)*size/32+.5))
                row.extend([round(235+(19-235)*blend),round(246+(184-246)*blend),round(240+(172-240)*blend),round(alpha*255)])
            rows.append(row)
        def chunk(tag,data):return struct.pack('>I',len(data))+tag+data+struct.pack('>I',zlib.crc32(tag+data))
        return b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',size,size,8,6,0,0,0))+chunk(b'IDAT',zlib.compress(b''.join(rows),9))+chunk(b'IEND',b'')
    frames=[(size,png(size)) for size in [16,32,48,256]]
    data=bytearray(struct.pack('<HHH',0,1,len(frames)));offset=6+16*len(frames)
    for size,payload in frames:data.extend(struct.pack('<BBBBHHII',size%256,size%256,0,0,1,32,len(payload),offset));offset+=len(payload)
    for _,payload in frames:data.extend(payload)
    target=ROOT/'windows/Kryra.App/Assets/Kryra.ico';target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(data)

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sdk',type=Path)
    parser.add_argument('--framework-dependent',action='store_true')
    parser.add_argument('--icon-only',action='store_true')
    parser.add_argument('--package-only',action='store_true',help='Refresh documentation and ZIP using the existing published binaries.')
    args=parser.parse_args()
    if args.package_only and (args.framework_dependent or args.icon_only):parser.error('--package-only cannot change the published runtime or icon')
    if not args.package_only:icon()
    if args.icon_only:return
    fallback=ROOT.parent/'mq80-v3-preparation/tools/dotnet-sdk-8.0.426/dotnet.exe'
    sdk=args.sdk or (fallback if fallback.exists() else Path(shutil.which('dotnet') or 'dotnet'))
    env=dict(os.environ,DOTNET_CLI_TELEMETRY_OPTOUT='1',DOTNET_SKIP_FIRST_TIME_EXPERIENCE='1',DOTNET_GENERATE_ASPNET_CERTIFICATE='false',DOTNET_CLI_HOME=str(ROOT/'build/dotnet-home'))
    def run(*parameters):subprocess.run([str(sdk),*parameters],cwd=ROOT,env=env,check=True)
    output=ROOT/'dist/Kryra-Windows';output.mkdir(parents=True,exist_ok=True)
    if args.package_only:
        existing=json.loads((output/'BuildInfo.json').read_text(encoding='utf-8'))
        if existing['version']!=VERSION:raise RuntimeError('Version changed; publish the application before packaging.')
        for item in existing['files']:
            if item['file'].lower().endswith(('.exe','.dll','.deps.json','.runtimeconfig.json')):
                path=output/item['file']
                if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=item['sha256']:
                    raise RuntimeError('Existing published binary changed: '+item['file'])
        for name in ['Kryra.exe','kryractl.exe']:
            if not (output/name).is_file():raise RuntimeError('Publish the application before packaging: '+name)
        sdk_version=existing['sdk'];self_contained=existing['self_contained']
    else:
        for project in ['Kryra.App','Kryra.Cli']:
            run('publish',f'windows/{project}/{project}.csproj','-c','Release','-r','win-x64','--self-contained',str(not args.framework_dependent).lower(),'-p:PublishSingleFile=false','-p:DebugType=None','--nologo','-o',str(output))
        sdk_version=subprocess.check_output([str(sdk),'--version'],env=env,text=True).strip();self_contained=not args.framework_dependent
    shutil.copy2(ROOT/'windows/API.html',output/'使用说明.html')
    shutil.copy2(ROOT/'windows/README.md',output/'README.md')
    shutil.copy2(ROOT/'windows/THIRD_PARTY_NOTICES.md',output/'THIRD_PARTY_NOTICES.md')
    shutil.copy2(ROOT/'DISCLAIMER.md',output/'免责声明.md')
    shutil.copytree(ROOT/'windows/licenses',output/'licenses',dirs_exist_ok=True)
    validation=ROOT/'windows/VALIDATION.json'
    if validation.exists():shutil.copy2(validation,output/'VALIDATION.json')
    files=[{'file':p.relative_to(output).as_posix(),'size':p.stat().st_size,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()} for p in sorted(output.rglob('*')) if p.is_file() and p.name!='BuildInfo.json']
    source_commit=existing.get('source_commit') if args.package_only else subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
    (output/'BuildInfo.json').write_text(json.dumps({'app':'Kryra Windows','version':VERSION,'runtime':'win-x64','self_contained':self_contained,'sdk':sdk_version,'source_commit':source_commit,'files':files},indent=2)+'\n',encoding='utf-8')
    archive=ROOT/f'dist/Kryra-Windows-{VERSION}-win-x64.zip'
    with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as package:
        for p in sorted(output.rglob('*')):
            if p.is_file():package.write(p,Path('Kryra-Windows')/p.relative_to(output))
    (archive.with_suffix('.sha256')).write_text(hashlib.sha256(archive.read_bytes()).hexdigest()+'  '+archive.name+'\n',encoding='ascii')
    print(json.dumps({'application':str(output/'Kryra.exe'),'archive':str(archive),'bytes':archive.stat().st_size},ensure_ascii=False))

if __name__=='__main__':main()
