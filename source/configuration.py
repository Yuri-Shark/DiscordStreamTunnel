"""Local configuration conversion; secrets are never printed."""
import base64
import configparser
import ipaddress
import json
from pathlib import Path
import secrets
import os
import sys

DEFAULT_HOME=(Path(os.environ.get('LOCALAPPDATA',str(Path.home()/'AppData'/'Local')))/'DiscordStreamTunnel') if sys.platform=='win32' else Path(os.environ.get('XDG_DATA_HOME',str(Path.home()/'.local'/'share')))/'discord-stream-tunnel'
ROOT=Path(os.environ.get('DTS_ROOT',str(DEFAULT_HOME))).resolve()
BASE=Path(os.environ.get('DISCORD_STREAM_TUNNEL_DATA',str(ROOT/'data'))).resolve()
CORE=ROOT/'core'/('sing-box.exe' if sys.platform=='win32' else 'sing-box')
def local_settings(base=BASE):
    base.mkdir(parents=True,exist_ok=True)
    path=base/'local-settings.json'
    if not path.exists():
        path.write_text(json.dumps({'relayPort':19884,'socksPort':19885,'nonce':secrets.token_urlsafe(32),'idleSeconds':40},indent=2),encoding='utf-8')
    return json.loads(path.read_text(encoding='utf-8'))

def key(value):
    try:
        if len(base64.b64decode(value,validate=True))!=32: raise ValueError()
    except Exception: raise ValueError('Invalid WireGuard key format') from None
    return value

def make_core_config(source,settings):
    parser=configparser.ConfigParser(interpolation=None,strict=True)
    parser.read_string(source)
    if set(parser.sections())!={'Interface','Peer'}: raise ValueError('Expected one Interface and one Peer')
    interface,peer=parser['Interface'],parser['Peer']
    addresses=[str(ipaddress.ip_interface(x.strip())) for x in interface['Address'].split(',')]
    endpoint=peer['Endpoint'].strip()
    host,port=endpoint.rsplit(':',1)
    host=host.strip('[]')
    try:
        address=ipaddress.ip_address(host)
        if not address.is_global: raise ValueError('Non-public WireGuard endpoint')
    except ValueError:
        # Proton exports numerical endpoints. Reject arbitrary hostnames and local targets.
        raise ValueError('A public numerical WireGuard endpoint is required') from None
    port=int(port)
    if not 1<=port<=65535: raise ValueError('Invalid endpoint port')
    allowed=[str(ipaddress.ip_network(x.strip(),strict=False)) for x in peer.get('AllowedIPs','0.0.0.0/0,::/0').split(',')]
    dns=str(ipaddress.ip_address(interface.get('DNS','10.2.0.1').split(',')[0].strip()))
    remote={'address':host,'port':port,'public_key':key(peer['PublicKey']),'allowed_ips':allowed,'persistent_keepalive_interval':int(peer.get('PersistentKeepalive','25'))}
    if 'PresharedKey' in peer: remote['pre_shared_key']=key(peer['PresharedKey'])
    return {'log':{'disabled':True},'inbounds':[{'type':'socks','tag':'streams-only','listen':'127.0.0.1','listen_port':settings['socksPort'],'users':[{'username':'discord-stream','password':settings['nonce']}]}],
        'endpoints':[{'type':'wireguard','tag':'proton','system':False,'mtu':min(int(interface.get('MTU','1280')),1408),'address':addresses,'private_key':key(interface['PrivateKey']),'peers':[remote],'workers':2}],
        'dns':{'servers':[{'type':'udp','tag':'tunnel-dns','server':dns,'detour':'proton'}]},
        'route':{'final':'proton','default_domain_resolver':{'server':'tunnel-dns','strategy':'ipv4_only'}}}

def install_wireguard(path):
    settings=local_settings()
    source=Path(path).read_text(encoding='utf-8-sig')
    config=make_core_config(source,settings)
    target=BASE/'private-core.json'
    target.write_text(json.dumps(config,indent=2),encoding='utf-8')
    return {'installed':True,'configurationFile':str(target),'systemInterface':False,'systemDNSChanged':False}

if __name__=='__main__':
    import sys
    try:
        print(json.dumps(install_wireguard(sys.argv[1]) if len(sys.argv)>1 else {'localSettingsReady':bool(local_settings())}))
    except Exception as error:
        print(json.dumps({'ok':False,'errorClass':type(error).__name__,'message':'Configuration could not be imported; no secret values displayed'}))
        sys.exit(1)
