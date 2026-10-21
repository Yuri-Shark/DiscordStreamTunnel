"""Stable executable entry: select immutable version before loading app code."""
import argparse
import importlib.abc
import importlib.machinery
import json
import os
from pathlib import Path
import re
import runpy
import sys

def main():
    parser=argparse.ArgumentParser(add_help=False);parser.add_argument('--root');parser.add_argument('--version')
    args,remaining=parser.parse_known_args()
    default=Path(os.environ.get('LOCALAPPDATA',str(Path.home()/'AppData'/'Local')))/'DiscordStreamTunnel' if sys.platform=='win32' else Path(os.environ.get('XDG_DATA_HOME',str(Path.home()/'.local'/'share')))/'discord-stream-tunnel'
    root=Path(args.root or os.environ.get('DTS_ROOT',str(default))).resolve();os.environ['DTS_ROOT']=str(root)
    payload=Path(getattr(sys,'_MEIPASS',Path(__file__).resolve().parent.parent))
    os.environ['DTS_PAYLOAD']=str(payload)
    version=args.version
    installed_entry=(Path(sys.executable).resolve().parent==root/'app') if getattr(sys,'frozen',False) else Path(__file__).resolve().parent==root
    if version is None and installed_entry and (root/'active.json').exists():version=json.loads((root/'active.json').read_text())['version']
    if version is not None:
        if not re.fullmatch(r'\d+\.\d+\.\d+',version):raise ValueError('InvalidVersion')
        source=root/'versions'/version/'source'
    else:source=payload/'source'
    if not source.is_dir():raise RuntimeError('VersionNotInstalled')
    vendor=source/'vendor';sys.path[:0]=[str(source),str(vendor)];sys.dont_write_bytecode=True
    names={p.stem for p in source.glob('*.py')}|{'websockets','python_socks','async_timeout'}
    class ExternalModules(importlib.abc.MetaPathFinder):
        def find_spec(self,fullname,path=None,target=None):
            if fullname.split('.')[0] in names:return importlib.machinery.PathFinder.find_spec(fullname,path or [str(source),str(vendor)])
    sys.meta_path.insert(0,ExternalModules())
    sys.argv=[str(source/'app.py'),*remaining]
    runpy.run_path(str(source/'app.py'),run_name='__main__')
if __name__=='__main__':main()
