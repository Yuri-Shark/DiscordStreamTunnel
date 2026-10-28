"""Allowlisted transport diagnostics suitable for sharing with support."""
import json
from pathlib import Path
import re
import time


NUMBERS={'updatedAt','warmupMs','lastWarmupAgeSeconds','coreGeneration','activeControls','mediaBridges',
    'openedControls','controlUpBytes','controlDownBytes','udpUpBytes','udpDownBytes','udpUpPackets','udpDownPackets',
    'udpQueueDrops','udpReceiveQueueDrops','udpMalformedPackets','udpDiscoveryRetries','controlRetries',
    'mediaStartupTimeouts','mediaResets','coreRestarts','lastUdpDownAt','lastTunnelCheckAt'}
FLAGS={'configurationReady','coreRunning','stopping','keepWarm','tunnelReachable'}
STREAM_FLAGS={'active','controlConnected','mediaAllocated','localPeerSeen','udpSent','udpReceived'}
EVENTS={'warmup-ok','warmup-failed','tunnel-probe-failed','tunnel-probe-ok','core-restart','core-restart-failed',
    'media-reset','control-open','control-close','control-error','control-retry','media-startup-timeout',
    'media-transport-closed','recovery-error','status-write-error'}


def number(value):
    return type(value) in (int,float) and 0<=value<1e18


def runtime_snapshot(base):
    file=Path(base)/'status.json'
    try:
        if file.stat().st_size>65536:return {'available':False}
        record=json.loads(file.read_text(encoding='utf-8'))
        if not isinstance(record,dict):return {'available':False}
    except (OSError,ValueError):return {'available':False}
    result={'available':True}
    for name in NUMBERS:
        value=record.get(name)
        if value is None or number(value):result[name]=value
    for name in FLAGS:
        value=record.get(name)
        if value is None or type(value) is bool:result[name]=value
    version=record.get('version')
    if isinstance(version,str) and re.fullmatch(r'\d+\.\d+\.\d+',version):result['version']=version
    error=record.get('lastErrorClass')
    if error is None or isinstance(error,str) and re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,63}',error):result['lastErrorClass']=error
    updated=result.get('updatedAt')
    result['stale']=updated is None or not 0<=time.time()-updated<=15
    result['streams']=[]
    streams=record.get('streams',[])
    for stream in streams[:8] if isinstance(streams,list) else []:
        if not isinstance(stream,dict):continue
        clean={name:value for name,value in stream.items() if name in STREAM_FLAGS and type(value) is bool}
        for name in ('controlConnectMs','discoveryMs'):
            value=stream.get(name)
            if value is None or number(value):clean[name]=value
        result['streams'].append(clean)
    result['recentEvents']=[]
    events=record.get('recentEvents',[])
    for event in events[-64:] if isinstance(events,list) else []:
        if not isinstance(event,dict) or not isinstance(event.get('event'),str) or event['event'] not in EVENTS:continue
        clean={'event':event['event']}
        for name in ('at','elapsedMs','code','opcode'):
            if number(event.get(name)):clean[name]=event[name]
        value=event.get('errorClass')
        if isinstance(value,str) and re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,63}',value):clean['errorClass']=value
        result['recentEvents'].append(clean)
    return result


def save_report(base,installation):
    report={name:installation[name] for name in ('version','vencordFound','integrationInstalled','configurationPresent','corePresent') if name in installation}
    report['runtime']=runtime_snapshot(base)
    destination=Path(base)/'diagnostico.json'
    destination.parent.mkdir(parents=True,exist_ok=True)
    temporary=destination.with_suffix('.tmp')
    temporary.write_text(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    temporary.replace(destination)
    return str(destination)
