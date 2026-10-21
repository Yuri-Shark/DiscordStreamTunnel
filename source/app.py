"""GUI installer and hidden stream worker; Python is bundled with this app."""
import argparse
import asyncio
import json
import os
from pathlib import Path
import sys
import subprocess

def emit(result,report=None):
    if report:Path(report).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    if sys.stdout is not None:print(json.dumps(result,ensure_ascii=True))

def gui(initial_action='install'):
    from installer_gui import run
    run(initial_action)


def main():
    if os.name!='nt':os.umask(0o077)
    parser=argparse.ArgumentParser()
    parser.add_argument('--data-dir');parser.add_argument('--report');parser.add_argument('--gui',action='store_true')
    sub=parser.add_subparsers(dest='command')
    install=sub.add_parser('install');install.add_argument('--config');install.add_argument('--headless',action='store_true');install.add_argument('--preload');install.add_argument('--core-archive');install.add_argument('--payload-dir')
    remove=sub.add_parser('uninstall');remove.add_argument('--headless',action='store_true');remove.add_argument('--preload')
    check=sub.add_parser('status');check.add_argument('--preload')
    relay=sub.add_parser('worker');relay.add_argument('--owner-pid',type=int);relay.add_argument('--idle-exit',action='store_true')
    launch=sub.add_parser('launch');launch.add_argument('arguments',nargs=argparse.REMAINDER)
    args=parser.parse_args()
    for name in ('config','preload','core_archive','payload_dir','headless'):
        if not hasattr(args,name):setattr(args,name,None)
    if args.data_dir:os.environ['DISCORD_STREAM_TUNNEL_DATA']=str(Path(args.data_dir).resolve())
    if args.command=='worker':
        from stream_relay import main as run
        asyncio.run(run(args.owner_pid,args.idle_exit));return 0
    if args.command=='launch':
        from installation import repair
        from configuration import ROOT
        repair()
        if sys.platform=='win32':
            updater=Path(os.environ['LOCALAPPDATA'])/'Discord'/'Update.exe'
            subprocess.Popen([str(updater),'--processStart','Discord.exe',*args.arguments],creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        else:subprocess.Popen(['discord',*[x for x in args.arguments if x!='--']],start_new_session=True)
        return 0
    if args.gui or sys.platform=='win32' and (args.command is None or args.command in ('install','uninstall') and not args.headless):
        try:gui(args.command or 'install')
        except ModuleNotFoundError as error:
            if error.name!='tkinter':raise
            emit({'ok':False,'message':'Para a interface gráfica no Linux, instale python3-tk (Ubuntu) ou tk (Arch/CachyOS). A instalação por terminal continua disponível.'},args.report);return 1
        return 0
    from installation import install as run_install,uninstall,status,InstallError
    if args.command is None:
        choice=input('1. Instalar / reaplicar\n2. Verificar\n3. Desinstalar\nEscolha: ').strip()
        args.command={'1':'install','2':'status','3':'uninstall'}.get(choice)
        if args.command is None:return 0
        if args.command=='install':args.config=input('Caminho do arquivo Proton .conf: ').strip().strip('"').strip("'")
    try:
        if args.command=='status':result=status(args.preload)
        elif args.command=='uninstall':result=uninstall(args.preload)
        else:
            if not args.config:raise InstallError('Escolha o arquivo .conf da sua conta Proton.')
            result=run_install(str(Path(args.config).expanduser()),args.preload,args.core_archive,args.payload_dir)
        emit({'ok':True,'result':result},args.report);return 0
    except InstallError as error:emit({'ok':False,'message':str(error)},args.report);return 1
    except Exception as error:emit({'ok':False,'errorClass':type(error).__name__,'message':'A operação falhou; nenhum dado privado foi exibido.'},args.report);return 1

if __name__=='__main__':
    try:code=main()
    except Exception as error:
        report=None
        if '--report' in sys.argv:
            index=sys.argv.index('--report')+1
            if index<len(sys.argv):report=sys.argv[index]
        try:emit({'ok':False,'errorClass':type(error).__name__,'message':'Não foi possível iniciar o programa.'},report)
        except Exception:pass
        code=1
    raise SystemExit(code)
