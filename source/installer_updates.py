"""Signed installer downloads and persistent, verified handoff to a newer package."""
import base64
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import urllib.request
from urllib.parse import urlparse
import uuid
import zipfile

from signature_verify import verify

REPOSITORY = 'Yuri-Shark/DiscordStreamTunnel'
ENDPOINT = 'https://github.com/'+REPOSITORY+'/releases/latest/download/update-manifest-v2.json'
VERSION = re.compile(r'^\d+\.\d+\.\d+$')
HOSTS = {'github.com','release-assets.githubusercontent.com','objects.githubusercontent.com'}
CACHE = Path(os.environ.get('LOCALAPPDATA',str(Path.home()/'.cache')))/'DiscordStreamTunnelInstaller'


def newer(candidate,current):
    return bool(VERSION.fullmatch(candidate) and VERSION.fullmatch(current) and tuple(map(int,candidate.split('.')))>tuple(map(int,current.split('.'))))


def trusted_url(url):
    parsed = urlparse(url)
    if parsed.scheme!='https' or parsed.hostname not in HOSTS or parsed.username or parsed.password or parsed.port:
        raise ValueError('UnexpectedDownloadHost')
    return url


def fetch(url,limit,progress=lambda _:None):
    class Redirects(urllib.request.HTTPRedirectHandler):
        max_redirections = 5
        def redirect_request(self,request,fp,code,msg,headers,newurl):
            return super().redirect_request(request,fp,code,msg,headers,trusted_url(newurl))
    request = urllib.request.Request(trusted_url(url),headers={'User-Agent':'DiscordStreamTunnel-Installer','Accept':'application/octet-stream'})
    with urllib.request.build_opener(Redirects()).open(request,timeout=8) as response:
        body = bytearray()
        while True:
            chunk = response.read(262144)
            if not chunk:break
            body.extend(chunk)
            if len(body)>limit:raise ValueError('DownloadTooLarge')
            if limit>65536:progress('Baixando o instalador… '+str(len(body)//1024)+' KB')
        return bytes(body)


def authenticate(envelope,trust=None):
    trust = trust or json.loads((Path(__file__).parent/'trust.json').read_text())
    pem = trust['publicKey'].replace('-----BEGIN PUBLIC KEY-----','').replace('-----END PUBLIC KEY-----','')
    der = base64.b64decode(''.join(pem.split()),validate=True)
    if len(der)!=44 or not der.startswith(bytes.fromhex('302a300506032b6570032100')):
        raise ValueError('InvalidPublicKey')
    message = base64.b64decode(envelope['signed'],validate=True)
    if len(message)>65536:raise ValueError('ManifestTooLarge')
    verify(der[-32:],base64.b64decode(envelope['signature'],validate=True),message)
    signed = json.loads(message)
    version = signed.get('version','')
    if signed.get('schema')!=1 or not isinstance(version,str) or not VERSION.fullmatch(version):raise ValueError('InvalidManifest')
    artifact = signed.get('installers',{}).get('win32')
    if artifact is None:return None
    expected = 'https://github.com/'+REPOSITORY+'/releases/download/v'+version+'/DiscordStreamTunnel-'+version+'-Windows-x64.zip'
    if (artifact.get('url')!=expected or type(artifact.get('bytes')) is not int or not 1<=artifact['bytes']<=64*1024*1024
            or not re.fullmatch(r'[a-f0-9]{64}',artifact.get('sha256',''))):raise ValueError('InvalidInstallerArtifact')
    return {'version':version,'artifact':artifact,'envelope':envelope}


def check(current):
    if sys.platform!='win32':return None
    release = authenticate(json.loads(fetch(ENDPOINT,65536)))
    return release if release and newer(release['version'],current) else None


def checked_archive(payload,release):
    artifact = release['artifact']
    if len(payload)!=artifact['bytes'] or hashlib.sha256(payload).hexdigest()!=artifact['sha256']:
        raise ValueError('InvalidInstallerHash')
    archive = zipfile.ZipFile(io.BytesIO(payload))
    if len(archive.infolist())>3000:raise ValueError('InstallerArchiveTooLarge')
    prefix = 'DiscordStreamTunnel-'+release['version']+'-Windows-x64'
    entries, names, total = [],set(),0
    for item in archive.infolist():
        parts = item.filename.rstrip('/').split('/')
        if (parts[0]!=prefix or '\\' in item.filename or any(not part or part in ('.','..') or ':' in part or part.endswith(('.', ' '))
                or re.fullmatch(r'(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?',part,re.I) for part in parts)
                or stat.S_IFMT(item.external_attr>>16) not in (0,stat.S_IFREG,stat.S_IFDIR)):
            raise ValueError('InvalidInstallerPath')
        if item.is_dir():continue
        name = '/'.join(parts[1:])
        if not name or name.casefold() in names:raise ValueError('DuplicateInstallerPath')
        names.add(name.casefold());total+=item.file_size
        if len(names)>2500 or total>192*1024*1024:raise ValueError('InstallerArchiveTooLarge')
        entries.append((item,name))
    if 'discordstreamtunnel.exe' not in names or not any(name.startswith('_internal/') for name in names):
        raise ValueError('IncompleteInstaller')
    return archive,entries


def prepare(release,cache=CACHE,progress=lambda _:None):
    # Reauthenticate even when called from the confirmation window.
    release = authenticate(release['envelope'])
    payload = fetch(release['artifact']['url'],release['artifact']['bytes'],progress)
    archive,entries = checked_archive(payload,release)
    cache = Path(cache).resolve()
    cache.mkdir(parents=True,exist_ok=True)
    destination = cache/('v'+release['version']+'-'+uuid.uuid4().hex)
    destination.mkdir(mode=0o700)
    try:
        progress('Assinatura e hash verificados. Preparando a nova versão…')
        (destination/'installer.zip').write_bytes(payload)
        folder = destination/'package'
        for item,name in entries:
            target = folder/name
            target.parent.mkdir(parents=True,exist_ok=True)
            target.write_bytes(archive.read(item))
        record = {'directory':destination.name,'envelope':release['envelope']}
        temp = cache/'current.new.json'
        temp.write_text(json.dumps(record),encoding='utf-8')
        temp.replace(cache/'current.json')
        return folder/'DiscordStreamTunnel.exe'
    except Exception:
        if destination.parent==cache and re.fullmatch(r'v\d+\.\d+\.\d+-[a-f0-9]{32}',destination.name):
            shutil.rmtree(destination)
        raise


def cached(current,cache=CACHE):
    try:
        cache = Path(cache).resolve()
        record = json.loads((cache/'current.json').read_text(encoding='utf-8'))
        release = authenticate(record['envelope'])
        if not release or not newer(release['version'],current):return None
        name = record['directory']
        if not re.fullmatch(r'v'+re.escape(release['version'])+r'-[a-f0-9]{32}',name):return None
        destination = cache/name
        if destination.is_symlink() or not destination.resolve().is_relative_to(cache):return None
        archive,entries = checked_archive((destination/'installer.zip').read_bytes(),release)
        folder = destination/'package'
        actual = {p.relative_to(folder).as_posix() for p in folder.rglob('*') if p.is_file()}
        if actual!={name for _,name in entries}:return None
        for item,name in entries:
            file = folder/name
            if file.is_symlink() or hashlib.sha256(file.read_bytes()).digest()!=hashlib.sha256(archive.read(item)).digest():return None
        return folder/'DiscordStreamTunnel.exe'
    except Exception:
        return None


def launch(executable):
    subprocess.Popen([str(executable)],cwd=str(executable.parent),creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
