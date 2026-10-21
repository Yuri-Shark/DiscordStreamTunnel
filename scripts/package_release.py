from pathlib import Path
import base64
import gzip
import hashlib
import json
import shutil
import stat
import sys
import tarfile
import zipfile
ROOT=Path(__file__).resolve().parents[1]
VERSION=json.loads((ROOT/'source'/'version.json').read_text())['version']
output=ROOT/'release';output.mkdir(exist_ok=True)
linux=output/('DiscordStreamTunnel-'+VERSION+'-linux');linux.mkdir(exist_ok=True)
shutil.copytree(ROOT/'source',linux/'source',dirs_exist_ok=True,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
for name in ('instalar.sh','README.md','LICENSE.txt'):shutil.copy2(ROOT/name,linux/name)
for file in linux.rglob('*'):
    if file.is_file():file.write_bytes(file.read_bytes().replace(b'\r\n',b'\n'))
def meta(info):
    info.uid=info.gid=0;info.uname=info.gname='';info.mode=0o755 if info.isdir() or info.name.endswith('.sh') else 0o644;return info
with tarfile.open(output/(linux.name+'.tar.gz'),'w:gz') as archive:archive.add(linux,arcname=linux.name,filter=meta)
files=[]
for file in sorted((linux/'source').rglob('*')):
    if file.is_file():
        raw=file.read_bytes();files.append({'path':file.relative_to(linux).as_posix(),'sha256':hashlib.sha256(raw).hexdigest(),'content':base64.b64encode(raw).decode()})
bundle={'version':VERSION,'files':files}
(output/'runtime-bundle.json.gz').write_bytes(gzip.compress(json.dumps(bundle,separators=(',',':')).encode(),mtime=0))
windows=output/('DiscordStreamTunnel-'+VERSION+'-Windows-x64');windows.mkdir(exist_ok=True)
shutil.copytree(ROOT/'dist'/'DiscordStreamTunnel',windows,dirs_exist_ok=True)
for name in ('README.md','LICENSE.txt'):shutil.copy2(ROOT/name,windows/name)
licenses=windows/'licencas';licenses.mkdir(exist_ok=True)
shutil.copy2(Path(sys.base_prefix)/'LICENSE.txt',licenses/'Python.txt')
shutil.copy2(Path(sys.base_prefix)/'tcl'/'tk8.6'/'license.terms',licenses/'Tk.txt')
for file in (linux/'source'/'vendor').rglob('*'):
    if file.is_file() and file.name.upper().startswith(('LICENSE','COPYING')):shutil.copy2(file,licenses/(file.parent.parent.name+'-'+file.name))
import importlib.metadata
dist=importlib.metadata.distribution('PyInstaller')
for file in dist.files or []:
    if file.name=='COPYING.txt':shutil.copy2(dist.locate_file(file),licenses/'PyInstaller.txt')
with zipfile.ZipFile(output/(windows.name+'.zip'),'w',compression=zipfile.ZIP_DEFLATED,compresslevel=9) as archive:
    for file in windows.rglob('*'):
        if file.is_file():archive.write(file,arcname=windows.name+'/'+file.relative_to(windows).as_posix())
print(json.dumps({'version':VERSION,'filesInRuntimeBundle':len(files)}))
