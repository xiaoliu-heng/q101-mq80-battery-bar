"""Compile and render the actual SwiftUI panel without launching a HID app."""
import subprocess
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'macos'))
from toolchain import Toolchain
toolchain=Toolchain()
BUILD=ROOT/'macos/build'
BUILD.mkdir(exist_ok=True)
sources=sorted(p for p in (ROOT/'macos/Kryra').glob('*.swift') if p.name!='App.swift')
flags=['-D','KRYRA_GLASS_SDK']
subprocess.run(toolchain.swift('-swift-version','5','-warnings-as-errors','-O','-parse-as-library',
                '-target','arm64-apple-macosx13.0',*flags,*map(str,sources),str(ROOT/'macos/tests/PanelTests.swift'),
                '-framework','AppKit','-framework','SwiftUI','-framework','IOKit','-framework','Network',
                '-framework','Security','-framework','ServiceManagement','-o',str(BUILD/'panel-tests')),check=True,cwd=ROOT)
subprocess.run([str(BUILD/'panel-tests'),str(ROOT/'test-results/kryra-panel-v1.3.4')],check=True,cwd=ROOT)
