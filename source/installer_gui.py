"""Original Tk interface inspired by the Vencord installer's layout."""
import json
import os
from pathlib import Path
import queue
import subprocess
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from configuration import ROOT
from installation import InstallError, VERSION, install, repair, status, target_preload, uninstall

BG = '#1e272e'
PANEL = '#27343e'
FIELD = '#34434e'
TEXT = '#eef1f5'
MUTED = '#b0bdc9'
BORDER = '#41515e'
PURPLE = '#635bff'
GREEN = '#2e854b'
RED = '#dc3e49'
REPOSITORY = 'https://github.com/Yuri-Shark/DiscordStreamTunnel'


class InstallerWindow:
    def __init__(self, root, initial_action='install'):
        self.root = root
        self.footer = None
        self.busy = False
        self.events = queue.Queue()
        self.current = {}
        self.buttons = {}
        self.update_dialog = None
        self.has_interacted = False
        self.file = tk.StringVar()
        self.message = tk.StringVar(value='Escolha sua configuração Proton para instalar. Se já instalou, você pode reparar a integração.')
        self.result_title = tk.StringVar(value='Pronto para começar')
        self.detection = tk.StringVar(value='Verificando a instalação…')
        self.local_version = tk.StringVar(value='—')
        self.updates = tk.StringVar(value='Verificando atualizações do instalador…' if os.name=='nt' else 'Atualizações da integração pelo Discord')
        root.title('DiscordStreamTunnel · Instalador')
        root.configure(bg=BG)
        root.geometry('1000x790')
        root.minsize(780, 620)
        self.font = 'Segoe UI' if os.name == 'nt' else 'DejaVu Sans'
        root.option_add('*Font', (self.font, 10))
        self.set_icon()
        style = ttk.Style(root)
        style.theme_use('clam')
        style.configure('Dts.Horizontal.TProgressbar', background=PURPLE, troughcolor=FIELD, borderwidth=0, lightcolor=PURPLE, darkcolor=PURPLE)

        footer = tk.Frame(root, bg=BG)
        self.footer = footer
        footer.pack(side='bottom', fill='x', padx=30, pady=(8,16))
        self.label(footer, 'v'+VERSION+'  ·  Yuri-Shark', size=9, color=MUTED).pack(side='left')
        self.link(footer, 'Instruções', REPOSITORY+'#instalar').pack(side='right')
        self.link(footer, 'GitHub', REPOSITORY).pack(side='right', padx=18)

        viewport = tk.Frame(root, bg=BG)
        viewport.pack(fill='both', expand=True)
        self.canvas = tk.Canvas(viewport, bg=BG, highlightthickness=0, bd=0)
        self.scrollbar = tk.Scrollbar(viewport, orient='vertical', command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.canvas.pack(side='left', fill='both', expand=True)
        content = tk.Frame(self.canvas, bg=BG)
        self.content = content
        self.window = self.canvas.create_window(0,0,window=content,anchor='nw')
        content.bind('<Configure>', self.resize_scroll)
        self.canvas.bind('<Configure>', self.resize_width)
        root.bind('<MouseWheel>', lambda e:self.canvas.yview_scroll(-int(e.delta/120),'units'))
        root.bind('<Next>', lambda _:self.canvas.yview_scroll(1,'pages'))
        root.bind('<Prior>', lambda _:self.canvas.yview_scroll(-1,'pages'))

        body = tk.Frame(content, bg=BG)
        body.pack(fill='both', expand=True, padx=34, pady=(24,8))
        header = tk.Frame(body, bg=BG)
        header.pack(pady=(0,8))
        if getattr(self, 'header_icon', None):
            tk.Label(header, image=self.header_icon, bg=BG).pack(side='left', padx=(0,14))
        self.label(header, 'DiscordStreamTunnel', size=24).pack(side='left')
        self.label(body, 'Instalador', size=11, color=MUTED).pack(pady=(0,20))

        directory = tk.Frame(body, bg=BG)
        directory.pack(fill='x')
        self.label(directory, 'Pasta de instalação', size=9, color=MUTED).pack(anchor='w')
        pathrow = tk.Frame(directory, bg=BG)
        pathrow.pack(fill='x', pady=(3,6))
        path = tk.Entry(pathrow, readonlybackground=BG, fg=TEXT, relief='flat', bd=0, highlightthickness=0,
                        textvariable=tk.StringVar(value=str(ROOT)), state='readonly', font=(self.font,9))
        path.pack(side='left', fill='x', expand=True)
        self.button(pathrow, 'Abrir pasta', self.open_directory, color=FIELD, small=True).pack(side='right', padx=(12,0))
        versions = tk.Frame(body, bg=BG)
        versions.pack(fill='x', pady=(4,16))
        self.label(versions, 'Versão instalada: ', color=MUTED, size=9).pack(side='left')
        self.label(versions, variable=self.local_version, size=9).pack(side='left')
        self.label(versions, '   ·   Instalador: '+VERSION, color=MUTED, size=9).pack(side='left')
        self.label(versions, variable=self.updates, color=MUTED, size=9).pack(side='right')
        tk.Frame(body, bg=BORDER, height=1).pack(fill='x', pady=(0,18))

        self.label(body, 'Instalação detectada', size=14).pack(anchor='w', pady=(0,10))
        detected = tk.Frame(body, bg=PANEL, highlightbackground=BORDER, highlightthickness=1, padx=16, pady=12)
        detected.pack(fill='x')
        line = tk.Frame(detected, bg=PANEL)
        line.pack(fill='x')
        self.dot = self.label(line, '●', color='#65d39a', bg=PANEL, size=13)
        self.dot.pack(side='left', padx=(0,9))
        self.label(line, 'Discord com Vencord', bg=PANEL, size=11).pack(side='left')
        self.label(line, variable=self.detection, color=MUTED, bg=PANEL, size=9).pack(side='right')
        self.label(detected, str(target_preload().parent), color=MUTED, bg=PANEL, size=9).pack(anchor='w', pady=(5,0))

        self.label(body, 'Configuração da sua conta Proton', size=14).pack(anchor='w', pady=(20,5))
        self.label(body, 'Selecione o arquivo WireGuard .conf. O aplicativo Proton não precisa ficar aberto.', color=MUTED, size=9).pack(anchor='w', pady=(0,10))
        picker = tk.Frame(body, bg=BG)
        picker.pack(fill='x')
        self.entry = tk.Entry(picker, textvariable=self.file, bg=FIELD, fg=TEXT, insertbackground=TEXT,
                              relief='flat', bd=0, highlightthickness=1, highlightbackground=BORDER,
                              highlightcolor='#938dff', font=(self.font,10))
        self.entry.pack(side='left', fill='both', expand=True, ipady=9)
        self.pick = self.button(picker, 'Escolher .conf', self.choose_file, color=FIELD)
        self.pick.pack(side='right', padx=(10,0))
        self.file.trace_add('write', lambda *_:self.refresh_controls())
        self.label(body, 'Feche o Discord antes de instalar, reparar ou remover.', color=MUTED, size=9).pack(anchor='w', pady=(9,15))

        actions = tk.Frame(body, bg=BG)
        actions.pack(fill='x', pady=(0,16))
        choices = [('Instalar / reaplicar','install',GREEN),('Reparar','repair',PURPLE),('Verificar','status',FIELD),('Desinstalar','uninstall',RED)]
        for index,(title,kind,color) in enumerate(choices):
            actions.columnconfigure(index, weight=1, uniform='action')
            button = self.button(actions, title, lambda k=kind:self.task(k), color=color)
            button.grid(row=0,column=index,sticky='ew',padx=(0 if index==0 else 6,0))
            self.buttons[kind] = button

        result = tk.Frame(body, bg=PANEL, padx=16, pady=12, highlightbackground=BORDER, highlightthickness=1)
        result.pack(fill='x')
        self.result_label = self.label(result, variable=self.result_title, bg=PANEL, size=10)
        self.result_label.pack(anchor='w')
        self.message_label = self.label(result, variable=self.message, bg=PANEL, color=MUTED, size=9)
        self.message_label.pack(anchor='w', fill='x', pady=(5,8))
        self.bar = ttk.Progressbar(result, mode='indeterminate', style='Dts.Horizontal.TProgressbar')
        self.bar.pack(fill='x')
        self.label(body, 'Depois da instalação, abra somente o Discord. O túnel inicia quando necessário.', color=MUTED, size=9).pack(anchor='w', pady=(12,0))
        root.protocol('WM_DELETE_WINDOW', self.close)
        root.after(80, self.initial_status)
        root.after(180, self.fit_window)
        if initial_action!='uninstall':root.after(250,self.start_update_check)
        root.after(120, self.poll)
        if initial_action=='uninstall':
            root.after(300, lambda:self.task('uninstall'))

    def label(self, parent, text=None, *, variable=None, size=10, color=TEXT, bg=BG):
        return tk.Label(parent, text=text, textvariable=variable, fg=color, bg=bg,
                        font=(self.font,size), anchor='w', justify='left')

    def button(self, parent, title, command, *, color=PURPLE, small=False):
        return tk.Button(parent, text=title, command=command, bg=color, fg='white',
                         activebackground=PURPLE, activeforeground='white', disabledforeground='#94a0ad',
                         relief='flat', bd=0, padx=12, pady=5 if small else 10, cursor='hand2',
                         highlightthickness=1, highlightbackground=color, highlightcolor='#b0aaff',
                         font=(self.font,9 if small else 10), takefocus=True)

    def link(self, parent, title, url):
        label = tk.Label(parent, text=title, fg='#b3adff', bg=BG, cursor='hand2', font=(self.font,9), takefocus=True)
        label.bind('<Button-1>', lambda _:self.open_url(url))
        label.bind('<Return>', lambda _:self.open_url(url))
        return label

    @staticmethod
    def open_url(url):
        if os.name=='nt':
            os.startfile(url)
        else:
            subprocess.Popen(['xdg-open',url],start_new_session=True)

    def set_icon(self):
        try:
            brand = json.loads((Path(__file__).parent/'branding.json').read_text())
            self.icon = tk.PhotoImage(data=brand['windowIconPNG'])
            self.header_icon = tk.PhotoImage(data=brand['headerIconPNG'])
            self.root.iconphoto(True, self.icon)
        except (OSError,ValueError,KeyError,tk.TclError):
            pass

    def resize_width(self, event):
        self.canvas.itemconfigure(self.window, width=event.width)
        if hasattr(self, 'message_label'):
            self.message_label.configure(wraplength=max(250,event.width-110))

    def fit_window(self):
        self.root.update_idletasks()
        width = min(1040,self.root.winfo_screenwidth()-80)
        height = min(self.content.winfo_reqheight()+self.footer.winfo_reqheight()+54,self.root.winfo_screenheight()-100)
        height = max(min(700,self.root.winfo_screenheight()-100),height)
        self.root.minsize(min(780,width),min(620,height))
        left = max(0,(self.root.winfo_screenwidth()-width)//2)
        top = max(20,(self.root.winfo_screenheight()-height)//2-20)
        self.root.geometry(f'{width}x{height}+{left}+{top}')

    def resize_scroll(self, _=None):
        self.canvas.configure(scrollregion=self.canvas.bbox('all'))
        if self.content.winfo_reqheight()>self.canvas.winfo_height():
            self.scrollbar.pack(side='right', fill='y')
        else:
            self.scrollbar.pack_forget()

    def open_directory(self):
        directory = ROOT if ROOT.is_dir() else ROOT.parent
        if os.name=='nt':
            os.startfile(str(directory))
        else:
            subprocess.Popen(['xdg-open',str(directory)], start_new_session=True)

    def choose_file(self):
        self.has_interacted = True
        file = filedialog.askopenfilename(parent=self.root, title='Escolher configuração WireGuard da sua conta Proton', filetypes=[('Configuração WireGuard','*.conf')])
        if file:
            self.file.set(file)

    def initial_status(self):
        try:
            self.update_status(status())
        except Exception:
            self.detection.set('Não foi possível verificar')
        self.refresh_controls()

    def update_status(self, value):
        self.current = value
        found = value.get('vencordFound',False)
        installed = value.get('integrationInstalled',False)
        self.detection.set('Integração instalada' if installed else 'Pronto para instalar' if found else 'Vencord não encontrado')
        self.dot.configure(fg='#65d39a' if found else '#edb867')
        try:
            self.local_version.set(json.loads((ROOT/'active.json').read_text())['version'] if installed else 'Não instalada')
        except (OSError,ValueError,KeyError):
            self.local_version.set('—')

    def refresh_controls(self):
        if not self.buttons:
            return
        found = self.current.get('vencordFound',False)
        installed = self.current.get('integrationInstalled',False)
        for kind,button in self.buttons.items():
            available = not self.busy
            if kind=='install':
                available = available and found and bool(self.file.get().strip())
            elif kind in ('repair','uninstall'):
                available = available and installed
            button.configure(state='normal' if available else 'disabled')
        self.pick.configure(state='disabled' if self.busy else 'normal')
        self.entry.configure(state='disabled' if self.busy else 'normal')

    def task(self, kind):
        if self.busy:
            return
        self.has_interacted = True
        if kind=='install' and not self.file.get().strip():
            self.message.set('Escolha o arquivo .conf da sua conta Proton antes de instalar.')
            return
        if kind=='uninstall' and not messagebox.askyesno('Desinstalar DiscordStreamTunnel',
                'Remover a integração deste usuário e apagar sua configuração local?\n\nFeche o Discord antes de continuar.', parent=self.root):
            return
        self.busy = True
        self.result_title.set({'install':'Instalando…','repair':'Reparando…','status':'Verificando…','uninstall':'Removendo…'}[kind])
        self.result_label.configure(fg=TEXT)
        self.message.set('Aguarde a conclusão. Você pode acompanhar o progresso aqui.')
        self.bar.start(14)
        self.refresh_controls()
        selected = self.file.get().strip()
        def run():
            try:
                if kind=='install':
                    result = install(selected, progress=lambda text:self.events.put(('progress',text)))
                elif kind=='uninstall':
                    result = uninstall()
                elif kind=='repair':
                    if not (ROOT/'installation.json').is_file():
                        raise InstallError('A instalação está incompleta. Selecione seu .conf e use Instalar / reaplicar.')
                    repair()
                    result = status()
                else:
                    result = status()
                self.events.put(('done',(kind,result)))
            except InstallError as error:
                self.events.put(('error',str(error)))
            except Exception:
                self.events.put(('error','Não foi possível concluir. Feche o Discord, confira a conexão e tente novamente.'))
        threading.Thread(target=run,daemon=True).start()

    def poll(self):
        while not self.events.empty():
            event,value = self.events.get()
            if event=='installer-update':
                self.updates.set('Nova versão disponível: '+value['version'])
                self.show_update_dialog(value)
                continue
            if event=='installer-cached':
                if self.open_cached_update(value):return
                continue
            if event=='installer-current':
                self.updates.set('Instalador atualizado')
                continue
            if event=='installer-check-failed':
                self.updates.set('Sem verificação de atualização')
                continue
            if event=='installer-ready':
                try:
                    from installer_updates import cached,launch
                    verified = cached(VERSION)
                    if verified!=value:raise ValueError('CachedInstallerChanged')
                    launch(verified)
                    self.root.destroy()
                    return
                except Exception:
                    event,value = 'error','Não foi possível abrir a nova versão. O instalador atual continua disponível.'
            if event=='installer-error':
                event = 'error'
            if event=='progress':
                self.message.set(value)
                continue
            self.busy = False
            self.bar.stop()
            self.initial_status()
            if event=='error':
                if self.update_dialog is not None:
                    self.update_dialog.destroy()
                    self.update_dialog = None
                self.result_title.set('Não foi possível concluir')
                self.result_label.configure(fg='#ff939c')
                self.message.set(value)
                continue
            kind,result = value
            if kind=='install':
                title,text = 'Instalação concluída','Abra o Discord novamente e assista a uma transmissão para testar.'
            elif kind=='repair':
                title,text = 'Integração reparada','O carregador foi verificado. A integração será carregada na próxima abertura do Discord.'
            elif kind=='uninstall':
                title,text = 'Integração removida','A configuração privada local foi apagada. Seu arquivo .conf original foi preservado.'
            else:
                title = 'Verificação concluída'
                text = ' · '.join([('Vencord encontrado' if result['vencordFound'] else 'Vencord ausente'),
                                   ('Integração instalada' if result['integrationInstalled'] else 'Integração ausente'),
                                   ('Configuração presente' if result['configurationPresent'] else 'Configuração ausente')])
            self.result_title.set(title)
            self.result_label.configure(fg='#80dfad')
            self.message.set(text)
        self.root.after(120,self.poll)

    def start_update_check(self):
        if os.name!='nt':return
        def run():
            try:
                from installer_updates import cached,check
                accepted = cached(VERSION)
                if accepted:
                    self.events.put(('installer-cached',accepted))
                    return
                release = check(VERSION)
                self.events.put(('installer-update',release) if release else ('installer-current',None))
            except Exception:
                self.events.put(('installer-check-failed',None))
        threading.Thread(target=run,daemon=True).start()

    def open_cached_update(self,executable):
        if self.busy:
            self.root.after(500,lambda:self.open_cached_update(executable))
            return
        if self.has_interacted:
            self.updates.set('Uma atualização já foi preparada. Abra o instalador novamente para usá-la.')
            return
        try:
            from installer_updates import cached,launch
            if cached(VERSION)!=executable:raise ValueError('CachedInstallerChanged')
            launch(executable)
            self.root.destroy()
            return True
        except Exception:
            self.updates.set('Não foi possível abrir a atualização preparada')

    def show_update_dialog(self,release):
        if self.busy:
            self.root.after(500,lambda:self.show_update_dialog(release))
            return
        if self.update_dialog is not None:return
        dialog = tk.Toplevel(self.root)
        self.update_dialog = dialog
        dialog.title('Atualização do instalador')
        dialog.configure(bg=BG)
        dialog.transient(self.root)
        dialog.resizable(False,False)
        box = tk.Frame(dialog,bg=BG,padx=28,pady=24)
        box.pack(fill='both',expand=True)
        self.label(box,'Nova versão disponível',size=17).pack(anchor='w')
        self.label(box,'Instalador '+VERSION+' → '+release['version'],color='#b7b0ff').pack(anchor='w',pady=(8,12))
        text = self.label(box,'Baixe a versão nova e reabra o instalador. Sua instalação atual e a configuração Proton serão preservadas.',size=10,color=MUTED)
        text.configure(wraplength=580)
        text.pack(anchor='w',pady=(0,18))
        row = tk.Frame(box,bg=BG)
        row.pack(fill='x')
        def dismiss():
            if not self.busy:
                self.update_dialog = None
                dialog.destroy()
        def accept():
            if self.busy:return
            self.busy = True
            self.has_interacted = True
            self.result_title.set('Atualizando o instalador…')
            self.message.set('Baixando e verificando o pacote antes de abrir a nova versão.')
            self.bar.start(14)
            self.refresh_controls()
            for child in row.winfo_children():child.configure(state='disabled')
            text.configure(text='Aguarde o download e a verificação. A nova versão será aberta ao concluir.')
            def run():
                try:
                    from installer_updates import prepare
                    executable = prepare(release,progress=lambda value:self.events.put(('progress',value)))
                    self.events.put(('installer-ready',executable))
                except Exception:
                    self.events.put(('installer-error','Não foi possível verificar ou baixar a atualização. Nenhum instalador foi executado; tente novamente mais tarde.'))
            threading.Thread(target=run,daemon=True).start()
        self.button(row,'Agora não',dismiss,color=FIELD).pack(side='left')
        self.button(row,'Atualizar e reabrir',accept).pack(side='right')
        dialog.protocol('WM_DELETE_WINDOW',dismiss)
        dialog.update_idletasks()
        dialog.geometry('+'+str(self.root.winfo_rootx()+(self.root.winfo_width()-dialog.winfo_reqwidth())//2)+'+'+str(self.root.winfo_rooty()+150))
        dialog.grab_set()
        dialog.focus_force()

    def close(self):
        if not self.busy:
            self.root.destroy()


def run(initial_action='install'):
    root = tk.Tk()
    InstallerWindow(root, initial_action)
    root.mainloop()
