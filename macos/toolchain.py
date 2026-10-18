"""Select an Apple SDK/toolchain, including the isolated Kryra installation."""
import json
import os
import subprocess
from pathlib import Path


def output(*args):
    return subprocess.run(list(map(str,args)),check=True,capture_output=True,text=True).stdout.strip()


class Toolchain:
    def __init__(self, minimum=26):
        override=os.environ.get('KRYRA_TOOLCHAIN_DIR')
        local=Path.home()/'Library/Developer/Kryra/CommandLineTools-26.6'
        if override:
            self._local(Path(override))
        else:
            active_version=output('xcrun','--show-sdk-version')
            if int(active_version.split('.')[0])>=minimum:
                self.swiftc=Path(output('xcrun','--find','swiftc'))
                self.sdk=Path(output('xcrun','--show-sdk-path'))
                self.version=active_version
            elif local.is_dir():
                self._local(local)
            else:
                raise RuntimeError('macOS SDK 26+ is required. Install Xcode/CLT 26 or set KRYRA_TOOLCHAIN_DIR.')
        if int(self.version.split('.')[0])<minimum:
            raise RuntimeError('The selected macOS SDK must be version '+str(minimum)+' or newer.')
        self.compiler_version=output(self.swiftc,'--version').splitlines()[0]

    def _local(self, directory):
        self.swiftc=directory/'usr/bin/swiftc'
        alias=directory/'SDKs/MacOSX.sdk'
        if not alias.is_dir():
            raise RuntimeError('Expected an Apple CommandLineTools directory with SDKs/MacOSX.sdk: '+str(directory))
        self.sdk=alias.resolve()
        self.version=json.loads((self.sdk/'SDKSettings.json').read_text())['Version']

    def swift(self, *args):
        # Clang's link step also needs SDKROOT to encode the real SDK version
        # in LC_BUILD_VERSION; -sdk alone can leave the deployment target there.
        return ['/usr/bin/env','SDKROOT='+str(self.sdk),str(self.swiftc),'-sdk',str(self.sdk),*map(str,args)]

    def metadata(self):
        return {'sdk':self.version,'sdk_path':str(self.sdk),'compiler':self.compiler_version,
                'compiler_path':str(self.swiftc),'native_glass_compiled':True}
