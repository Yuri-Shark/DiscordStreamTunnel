from pathlib import Path
import py_compile
import json
import shutil
import subprocess
import sys
ROOT=Path(__file__).resolve().parents[1]
stage=ROOT/'build'/'payload'/'source'
if stage.exists():
    if stage.is_symlink() or not stage.resolve().is_relative_to((ROOT/'build').resolve()):raise RuntimeError('Unsafe build payload path')
    shutil.rmtree(stage)
stage.mkdir(parents=True,exist_ok=True)
for file in (ROOT/'source').iterdir():
    if file.is_file() and file.suffix in ('.py','.cjs','.js','.json','.ps1'):
        if file.suffix=='.py':py_compile.compile(str(file),doraise=True)
        shutil.copy2(file,stage/file.name)
shutil.copytree(ROOT/'source'/'vendor',stage/'vendor',dirs_exist_ok=True,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
version=json.loads((ROOT/'source'/'version.json').read_text())['version']
numbers=tuple(int(n) for n in version.split('.'))+(0,)
version_file=ROOT/'build'/'windows-version.txt'
version_file.write_text("VSVersionInfo(ffi=FixedFileInfo(filevers="+repr(numbers)+",prodvers="+repr(numbers)+",mask=0x3f,flags=0x0,OS=0x40004,fileType=0x1,subtype=0x0,date=(0,0)),kids=[StringFileInfo([StringTable('040904B0',["+
    ','.join('StringStruct('+repr(k)+','+repr(v)+')' for k,v in {
        'CompanyName':'Yuri-Shark','FileDescription':'DiscordStreamTunnel Installer and Local Helper',
        'FileVersion':version,'InternalName':'DiscordStreamTunnel','OriginalFilename':'DiscordStreamTunnel.exe',
        'ProductName':'DiscordStreamTunnel','ProductVersion':version,'LegalCopyright':'Copyright 2026 DiscordStreamTunnel contributors. MIT License.'}.items())+
    "])]),VarFileInfo([VarStruct('Translation',[1033,1200])])])",encoding='utf-8')
command=[sys.executable,'-m','PyInstaller','--clean','--noconfirm','--onedir','--windowed','--noupx','--name','DiscordStreamTunnel',
 '--icon',str(ROOT/'assets'/'app-icon.ico'),'--version-file',str(version_file),'--manifest',str(ROOT/'assets'/'windows-app.manifest'),
 '--distpath',str(ROOT/'dist'),'--workpath',str(ROOT/'build'/'pyinstaller'),'--specpath',str(ROOT/'build'),
 '--paths',str(ROOT/'source'),'--paths',str(ROOT/'source'/'vendor'),'--hidden-import','app','--hidden-import','installer_gui','--hidden-import','tkinter','--hidden-import','ssl',
 '--add-data',str(stage)+':source',str(ROOT/'source'/'entry.py')]
subprocess.run(command,check=True,cwd=ROOT)
