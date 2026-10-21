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
    import queue
    import threading
    import tkinter as tk
    from tkinter import filedialog,messagebox,ttk
    from installation import install,uninstall,status,InstallError
    root=tk.Tk();root.title('Transmissões no Discord');root.geometry('650x450');root.resizable(False,False)
    frame=ttk.Frame(root,padding=24);frame.pack(fill='both',expand=True)
    ttk.Label(frame,text='Transmissões no Discord',font=('Segoe UI',19,'bold')).pack(anchor='w')
    ttk.Label(frame,text='Integração local com Vencord e Proton WireGuard',font=('Segoe UI',10)).pack(anchor='w',pady=(3,18))
    ttk.Label(frame,text='Use uma configuração .conf da sua própria conta Proton.\nO aplicativo completo Proton não precisa ficar aberto.',justify='left').pack(anchor='w')
    file_var=tk.StringVar();row=ttk.Frame(frame);row.pack(fill='x',pady=(15,10))
    ttk.Entry(row,textvariable=file_var,width=60).pack(side='left',fill='x',expand=True)
    ttk.Button(row,text='Escolher .conf',command=lambda:file_var.set(filedialog.askopenfilename(title='Escolher configuração WireGuard',filetypes=[('WireGuard','*.conf')]) or file_var.get())).pack(side='right',padx=(8,0))
    label=tk.StringVar(value='Escolha o arquivo e clique em Instalar. O guia acompanha o ZIP.')
    ttk.Label(frame,textvariable=label,wraplength=590,justify='left').pack(anchor='w',pady=(3,12))
    bar=ttk.Progressbar(frame,mode='indeterminate');bar.pack(fill='x',pady=(0,12))
    actions=ttk.Frame(frame);actions.pack(fill='x')
    events=queue.Queue();buttons=[];busy=False
    def task(kind):
        nonlocal busy
        if busy:return
        if kind=='install' and not file_var.get():messagebox.showinfo('Arquivo necessário','Escolha o arquivo .conf gerado na sua conta Proton.',parent=root);return
        if kind=='uninstall' and not messagebox.askyesno('Desinstalar','Remover a integração deste usuário e apagar sua configuração local?\n\nFeche o Discord antes de continuar.',parent=root):return
        busy=True;bar.start(12)
        for button in buttons:button.configure(state='disabled')
        selected=file_var.get()
        def run():
            try:
                result=install(selected,progress=lambda text:events.put(('progress',text))) if kind=='install' else uninstall() if kind=='uninstall' else status()
                events.put(('done',(kind,result)))
            except InstallError as error:events.put(('error',str(error)))
            except Exception:events.put(('error','Não foi possível concluir. Feche o Discord, confira a conexão e tente novamente. Nenhuma chave foi exibida.'))
        threading.Thread(target=run,daemon=True).start()
    for title,kind in [('Instalar / reaplicar','install'),('Verificar instalação','status'),('Desinstalar','uninstall')]:
        button=ttk.Button(actions,text=title,command=lambda k=kind:task(k));button.pack(side='left',padx=(0,8));buttons.append(button)
    ttk.Label(frame,text='Versão 1.1.0 · recuperação após atualização do Vencord\nAtualizações assinadas no GitHub · túnel preparado ao abrir Discord',wraplength=590,foreground='#555555').pack(anchor='w',pady=(20,0))
    def poll():
        nonlocal busy
        while not events.empty():
            event,value=events.get()
            if event=='progress':label.set(value);continue
            busy=False;bar.stop()
            for button in buttons:button.configure(state='normal')
            if event=='error':label.set(value);messagebox.showerror('Instalação não concluída',value,parent=root)
            else:
                kind,result=value
                if kind=='install':text='Instalado. Feche o Discord por completo e abra novamente. Depois, abra uma transmissão para testar.'
                elif kind=='uninstall':text='Integração e configuração privada removidas. Feche e abra o Discord para descarregar a sessão atual.'
                else:text='Vencord: '+('encontrado' if result['vencordFound'] else 'não encontrado')+' | Integração: '+('instalada' if result['integrationInstalled'] else 'ausente')+' | Configuração: '+('presente' if result['configurationPresent'] else 'ausente')
                label.set(text);messagebox.showinfo('Transmissões no Discord',text,parent=root)
        root.after(120,poll)
    root.protocol('WM_DELETE_WINDOW',lambda:None if busy else root.destroy())
    root.after(100,poll)
    if initial_action=='uninstall':root.after(200,lambda:task('uninstall'))
    root.mainloop()

def main():
    if os.name!='nt':os.umask(0o077)
    parser=argparse.ArgumentParser()
    parser.add_argument('--data-dir');parser.add_argument('--report')
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
    if sys.platform=='win32' and (args.command is None or args.command in ('install','uninstall') and not args.headless):
        gui(args.command or 'install');return 0
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
