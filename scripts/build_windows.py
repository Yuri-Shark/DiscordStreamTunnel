from pathlib import Path
import py_compile
import shutil
import subprocess
import sys
ROOT=Path(__file__).resolve().parents[1]
stage=ROOT/'build'/'payload'/'source';stage.mkdir(parents=True,exist_ok=True)
for file in (ROOT/'source').iterdir():
    if file.is_file() and file.suffix in ('.py','.cjs','.js','.json','.ps1'):
        if file.suffix=='.py':py_compile.compile(str(file),doraise=True)
        shutil.copy2(file,stage/file.name)
shutil.copytree(ROOT/'source'/'vendor',stage/'vendor',dirs_exist_ok=True,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
command=[sys.executable,'-m','PyInstaller','--clean','--noconfirm','--onedir','--windowed','--noupx','--name','DiscordStreamTunnel',
 '--distpath',str(ROOT/'dist'),'--workpath',str(ROOT/'build'/'pyinstaller'),'--specpath',str(ROOT/'build'),
 '--paths',str(ROOT/'source'),'--paths',str(ROOT/'source'/'vendor'),'--hidden-import','app','--hidden-import','tkinter','--hidden-import','ssl',
 '--add-data',str(stage)+':source',str(ROOT/'source'/'entry.py')]
subprocess.run(command,check=True,cwd=ROOT)
