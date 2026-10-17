"""Build and run the native protocol/lifecycle tests, without device access."""
import subprocess
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
BUILD=ROOT/'macos/build'
BUILD.mkdir(exist_ok=True)
subprocess.run([sys.executable,str(ROOT/'macos/tests/fixtures.py')],check=True,cwd=ROOT)
sources=[ROOT/'macos/Kryra'/name for name in ['Protocol.swift','HID.swift','HTTP.swift','Controller.swift','RuntimeFiles.swift','SkillInstaller.swift']]
subprocess.run(['xcrun','swiftc','-swift-version','5','-warnings-as-errors','-O','-parse-as-library',
                '-target','arm64-apple-macosx13.0',*map(str,sources),str(ROOT/'macos/tests/CoreTests.swift'),
                '-framework','IOKit','-framework','Network','-framework','Security','-o',str(BUILD/'core-tests')],check=True,cwd=ROOT)
subprocess.run([str(BUILD/'core-tests'),str(BUILD/'wire-fixtures.json'),str(ROOT/'test-results/kryra-core-tests.json')],check=True,cwd=ROOT)
