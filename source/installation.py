"""Per-user installation. No account data ships in the distribution."""
import csv
import hashlib
import io
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import sys
import urllib.request
import zipfile
import tarfile
import platform
import re

from configuration import ROOT,BASE,CORE,make_core_config

VERSION=json.loads((Path(__file__).resolve().parent/'version.json').read_text())['version']
RESOURCE=Path(__file__).resolve().parent
START=b'\n/* DISCORD_STREAM_TUNNEL_V2_BEGIN */\n'
END=b'\n/* DISCORD_STREAM_TUNNEL_V2_END */\n'
RESTORE_START=b'\n/* DISCORD_STREAM_TUNNEL_RESTORE_V1 */\n'
RESTORE_END=b'\n/* DISCORD_STREAM_TUNNEL_RESTORE_END_V1 */\n'
LEGACY=b'DISCORD_PROTON_STREAMS_LOCAL_BEGIN'
class InstallError(Exception):pass

def target_preload(override=None):
    if override:return Path(override).expanduser().resolve()
    if sys.platform=='win32':return Path(os.environ['APPDATA'])/'Vencord'/'dist'/'preload.js'
    if os.environ.get('VENCORD_USER_DATA_DIR'):base=Path(os.environ['VENCORD_USER_DATA_DIR'])
    elif os.environ.get('DISCORD_USER_DATA_DIR'):base=Path(os.environ['DISCORD_USER_DATA_DIR']).parent/'VencordData'
    else:base=Path(os.environ.get('XDG_CONFIG_HOME',str(Path.home()/'.config')))/'Vencord'
    return base/'dist'/'preload.js'

def atomic_write(path,data):
    path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_name(path.name+'.new');temp.write_bytes(data)
    if os.name!='nt':temp.chmod(0o600)
    temp.replace(path)

def strip_block(data,start=START,end=END):
    if start not in data:return data
    if data.count(start)!=1 or data.count(end)!=1:raise InstallError('O bloco da integração foi alterado. Nenhum outro código foi removido.')
    before,rest=data.split(start,1);_,after=rest.split(end,1)
    return before+after

def secure_data_folder(base):
    base.mkdir(parents=True,exist_ok=True)
    if os.name!='nt':base.chmod(0o700);return
    flags=getattr(subprocess,'CREATE_NO_WINDOW',0)
    result=subprocess.run(['whoami.exe','/user','/fo','csv','/nh'],capture_output=True,check=True,creationflags=flags)
    sid=next(csv.reader(io.StringIO(result.stdout.decode('mbcs'))))[1]
    if not sid.startswith('S-1-'):raise InstallError('Não foi possível identificar o usuário do Windows.')
    result=subprocess.run(['icacls.exe',str(base),'/inheritance:r','/grant:r',f'*{sid}:(OI)(CI)F','*S-1-5-18:(OI)(CI)F'],capture_output=True,creationflags=flags)
    if result.returncode:raise InstallError('Não foi possível proteger a pasta de configuração. Execute com o seu usuário normal.')

def free_port(excluded=()):
    for _ in range(20):
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        if port not in excluded:return port
    raise InstallError('Não foi possível reservar portas locais.')

def settings_for(base):
    path=base/'local-settings.json'
    if path.exists():return json.loads(path.read_text())
    relay=free_port()
    settings={'relayPort':relay,'socksPort':free_port((relay,)),'nonce':secrets.token_urlsafe(32),'idleSeconds':40,'warmIdleSeconds':120}
    atomic_write(path,json.dumps(settings,indent=2).encode())
    return settings

def download_core(core=CORE,archive=None,progress=lambda _:None):
    arch={'x86_64':'amd64','amd64':'amd64','aarch64':'arm64','arm64':'arm64'}.get(platform.machine().lower())
    meta=json.loads((RESOURCE/'core-download.json').read_text()).get(sys.platform+'-'+str(arch))
    if not meta:raise InstallError('Arquitetura não suportada.')
    if core.exists() and hashlib.sha256(core.read_bytes()).hexdigest()==meta['executableSHA256']:return
    progress('Baixando o núcleo de rede oficial...')
    if archive:
        payload=Path(archive).read_bytes()
    else:
        request=urllib.request.Request(meta['source'],headers={'User-Agent':'DiscordStreamTunnel/1.0'})
        try:
            with urllib.request.urlopen(request,timeout=60) as response:
                chunks=[];size=0
                while True:
                    chunk=response.read(524288)
                    if not chunk:break
                    size+=len(chunk)
                    if size>150_000_000:raise InstallError('O download ultrapassou o tamanho esperado.')
                    chunks.append(chunk)
                payload=b''.join(chunks)
        except InstallError:raise
        except Exception:raise InstallError('Não foi possível baixar o núcleo no GitHub. Verifique a conexão e tente novamente.') from None
    if hashlib.sha256(payload).hexdigest()!=meta['archiveSHA256']:raise InstallError('O download não passou na verificação SHA256. Nenhum núcleo foi executado.')
    progress('Verificando o núcleo de rede...')
    if sys.platform=='win32':
        with zipfile.ZipFile(io.BytesIO(payload)) as package:
            matches=[x for x in package.infolist() if x.filename.endswith('/sing-box.exe')]
            if len(matches)!=1:raise InstallError('Estrutura inesperada no download oficial.')
            executable=package.read(matches[0])
    else:
        with tarfile.open(fileobj=io.BytesIO(payload),mode='r:gz') as package:
            matches=[x for x in package.getmembers() if x.isfile() and x.name.endswith('/sing-box')]
            if len(matches)!=1:raise InstallError('Estrutura inesperada no download oficial.')
            executable=package.extractfile(matches[0]).read()
    if hashlib.sha256(executable).hexdigest()!=meta['executableSHA256']:raise InstallError('O executável do núcleo não passou na verificação.')
    atomic_write(core,executable)
    if os.name!='nt':core.chmod(0o700)
    atomic_write(core.parent/'origin.json',json.dumps(meta,indent=2).encode())

def copy_application(destination,source_payload=None):
    origin=Path(source_payload).resolve() if source_payload else Path(sys.executable).resolve().parent
    if sys.platform!='win32':
        shutil.copy2(RESOURCE/'entry.py',ROOT/'launcher.py');return
    if not getattr(sys,'frozen',False) and not source_payload:raise InstallError('Use o executável empacotado para instalar.')
    if origin==destination.resolve():return
    if not (origin/'DiscordStreamTunnel.exe').is_file():raise InstallError('O pacote está incompleto. Extraia o ZIP inteiro antes de instalar.')
    destination.mkdir(parents=True,exist_ok=True)
    shutil.copy2(origin/'DiscordStreamTunnel.exe',destination/'DiscordStreamTunnel.exe')
    if (origin/'_internal').is_dir():shutil.copytree(origin/'_internal',destination/'_internal',dirs_exist_ok=True)

def install(config_path,preload_override=None,archive=None,source_payload=None,progress=lambda _:None):
    base=BASE;preload=target_preload(preload_override)
    if not preload.is_file():raise InstallError('Vencord não encontrado. Instale-o no Discord para desktop antes de continuar.')
    original=preload.read_bytes()
    if b'Vencord' not in original[:1500]:raise InstallError('O arquivo selecionado não parece ser o preload do Vencord.')
    receipt_path=ROOT/'installation.json'
    if START in original and not receipt_path.exists():raise InstallError('Já existe uma instalação deste sistema em outra pasta. Desinstale-a primeiro.')
    if receipt_path.exists() and str(preload.resolve())!=json.loads(receipt_path.read_text())['preload']:
        raise InstallError('Esta instalação pertence a outro perfil Vencord.')
    # Never expose parser exceptions: malformed input may contain a private key.
    try:
        source=Path(config_path).read_text(encoding='utf-8-sig')
        make_core_config(source,{'socksPort':19885,'nonce':'validation-only'})
    except Exception:raise InstallError('Arquivo WireGuard inválido. Escolha o .conf gerado na sua conta Proton.') from None
    secure_data_folder(base)
    settings=settings_for(base)
    config=make_core_config(source,settings)
    progress('Preparando os arquivos locais...')
    copy_application(base.parent/'app',source_payload)
    download_core(CORE,archive,progress)
    # Validate secrets in a private staging file; never print core output.
    staged=base/'private-core.pending.json'
    atomic_write(staged,json.dumps(config,indent=2).encode())
    result=subprocess.run([str(CORE),'check','-c',str(staged)],capture_output=True,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    if result.returncode:
        staged.unlink(missing_ok=True);raise InstallError('O núcleo recusou a configuração. Nenhuma alteração foi feita no Vencord.')
    staged.replace(base/'private-core.json')
    version_path=ROOT/'versions'/VERSION/'source'
    if RESOURCE.resolve()!=version_path.resolve():shutil.copytree(RESOURCE,version_path,dirs_exist_ok=True,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    for name in ('bootstrap.cjs','trust.json'):atomic_write(ROOT/name,(RESOURCE/name).read_bytes())
    atomic_write(ROOT/'active.json',json.dumps({'version':VERSION}).encode())
    clean=strip_block(strip_block(original),RESTORE_START,RESTORE_END)
    for start,end in ((b'\n/* DISCORD_PROTON_STREAMS_LOCAL_BEGIN */\n',b'\n/* DISCORD_PROTON_STREAMS_LOCAL_END */\n'),(b'\n/* DISCORD_STREAM_TUNNEL_BEGIN_V1 */\n',b'\n/* DISCORD_STREAM_TUNNEL_END_V1 */\n')):
        clean=strip_block(clean,start,end)
    backup=base/('preload-before-'+hashlib.sha256(clean).hexdigest()[:16]+'.js')
    if not backup.exists():atomic_write(backup,clean)
    statement='try{require('+json.dumps(str(ROOT/'bootstrap.cjs'))+');}catch{}'
    patched=clean+START+statement.encode()+END
    progress('Ativando a integração no Vencord...')
    mode=preload.stat().st_mode&0o777;atomic_write(preload,patched)
    if os.name!='nt':preload.chmod(mode)
    worker=str(ROOT/'app'/'DiscordStreamTunnel.exe') if sys.platform=='win32' else os.path.abspath(sys.executable)
    receipt={'version':VERSION,'preload':str(preload.resolve()),'backup':backup.name,'worker':worker,'workerPrefix':[] if sys.platform=='win32' else [str(ROOT/'launcher.py')]}
    atomic_write(receipt_path,json.dumps(receipt,indent=2).encode())
    if preload_override is None and source_payload is None:launchers(False)
    return {'installed':True,'version':VERSION,'dataDirectory':str(base),'restartDiscordRequired':True,'systemRoutesChanged':False}

def repair():
    receipt=json.loads((ROOT/'installation.json').read_text())
    preload=Path(receipt['preload']);data=preload.read_bytes()
    if b'Vencord' not in data[:1500]:raise InstallError('Preload inesperado; nenhuma alteração foi feita.')
    clean=strip_block(data);patched=clean+START+('try{require('+json.dumps(str(ROOT/'bootstrap.cjs'))+');}catch{}').encode()+END
    if data!=patched:
        backup=BASE/('preload-before-'+hashlib.sha256(clean).hexdigest()[:16]+'.js')
        if not backup.exists():atomic_write(backup,clean)
        mode=preload.stat().st_mode&0o777;atomic_write(preload,patched)
        if os.name!='nt':preload.chmod(mode)
    return data!=patched

def launchers(remove=False):
    if sys.platform=='win32':
        if not getattr(sys,'frozen',False):return
        try:
            from windows_shortcuts import manage
            manage(ROOT,remove)
        except Exception:atomic_write(BASE/'launcher-status.json',b'{"configured":false}')
        return
    applications=Path(os.environ.get('XDG_DATA_HOME',str(Path.home()/'.local'/'share')))/'applications'
    target=applications/'discord.desktop';backup=BASE/'desktop-before.bin';record=BASE/'desktop-created.json'
    if remove:
        if target.exists() and 'X-DiscordStreamTunnel=true' in target.read_text():
            if backup.exists():target.write_bytes(backup.read_bytes())
            else:target.unlink()
        return
    if target.exists() and 'X-DiscordStreamTunnel=true' not in target.read_text() and not backup.exists():atomic_write(backup,target.read_bytes())
    def quoted(value):return '"'+str(value).replace('\\','\\\\').replace('"','\\"').replace('`','\\`').replace('$','\\$')+'"'
    command=' '.join(quoted(x) for x in (os.path.abspath(sys.executable),ROOT/'launcher.py','--root',ROOT,'launch','--'))+' %U'
    content='[Desktop Entry]\nType=Application\nName=Discord\nExec='+command+'\nIcon=discord\nTerminal=false\nStartupWMClass=discord\nCategories=Network;InstantMessaging;\nX-DiscordStreamTunnel=true\n'
    applications.mkdir(parents=True,exist_ok=True);target.write_text(content);target.chmod(0o644)

def status(preload_override=None):
    preload=target_preload(preload_override)
    return {'version':VERSION,'vencordFound':preload.exists(),'integrationInstalled':preload.exists() and START in preload.read_bytes(),
        'configurationPresent':(BASE/'private-core.json').exists(),'corePresent':CORE.exists(),'dataDirectory':str(BASE)}

def uninstall(preload_override=None):
    if preload_override is None:launchers(True)
    receipt_path=ROOT/'installation.json'
    receipt=json.loads(receipt_path.read_text()) if receipt_path.exists() else {}
    preload=target_preload(preload_override or receipt.get('preload'))
    if preload.exists():
        data=preload.read_bytes();clean=strip_block(data)
        backup=BASE/'video-guard-before.json'
        if clean!=data and backup.exists():
            variant=json.loads(backup.read_text()).get('variantId')
            if variant is not None and (type(variant) is not int or not 0<=variant<=100):raise InstallError('Backup da configuração de vídeo inválido.')
            event={'type':'APEX_EXPERIMENT_OVERRIDE_DELETE','experimentName':'2026-08-video-guard'} if variant is None else {'type':'APEX_EXPERIMENT_OVERRIDE_CREATE','experimentName':'2026-08-video-guard','variantId':variant}
            restore="(async()=>{for(let n=0;n<100;n++){const W=window.Vencord?.Webpack,s=W?.findStore('ApexExperimentStore');if(s){if(s.getState().clientOverrides?.['2026-08-video-guard']?.variantId===0){W.Common.FluxDispatcher.dispatch("+json.dumps(event)+");s.persist?.();}return true;}await new Promise(r=>setTimeout(r,300));}return false;})()"
            # Self-removing restoration requires only Electron/Node, never the helper.
            statement="try{require('electron/renderer').webFrame.executeJavaScript("+json.dumps(restore)+").then(ok=>{if(ok){const fs=require('fs'),p="+json.dumps(str(preload.resolve()))+",s=fs.readFileSync(p,'utf8'),a="+json.dumps(RESTORE_START.decode())+",b="+json.dumps(RESTORE_END.decode())+",i=s.indexOf(a),j=s.indexOf(b,i+a.length);if(i>=0&&j>=0)fs.writeFileSync(p,s.slice(0,i)+s.slice(j+b.length),'utf8');}});}catch{}"
            clean=strip_block(clean,RESTORE_START,RESTORE_END)+RESTORE_START+statement.encode()+RESTORE_END
        if clean!=data:atomic_write(preload,clean)
    # Only remove known secret files in our explicitly selected data directory.
    for name in ('private-core.json','private-core.pending.json','runtime.expr.js','local-settings.json','installation.json','video-guard-before.json'):
        (BASE/name).unlink(missing_ok=True)
    receipt_path.unlink(missing_ok=True)
    return {'integrationRemoved':True,'privateConfigurationRemoved':True,'closeDiscordToUnloadCurrentSession':True,'programFilesRetainedForManualRemoval':str(BASE.parent)}
