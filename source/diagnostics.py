"""Allowlisted transport diagnostics suitable for sharing with support."""
import json
from pathlib import Path
import re
import sys
import time


NUMBERS={'updatedAt','warmupMs','lastWarmupAgeSeconds','coreGeneration','activeControls','mediaBridges',
    'openedControls','controlUpBytes','controlDownBytes','udpUpBytes','udpDownBytes','udpUpPackets','udpDownPackets',
    'udpQueueDrops','udpReceiveQueueDrops','udpMalformedPackets','udpDiscoveryRetries','controlRetries',
    'mediaStartupTimeouts','mediaResets','coreRestarts','lastUdpDownAt','lastTunnelCheckAt',
    'udpPathChecks','udpPathProbes','mediaPathTimeouts','mediaPathChanges','udpSocketErrors','udpSendBackpressure','udpSendTimeouts',
    'activeGatewayControls','openedGatewayControls','gatewayConnectMs','gatewayUpBytes','gatewayDownBytes','gatewayRetries'}
FLAGS={'configurationReady','coreRunning','stopping','keepWarm','tunnelReachable'}
STREAM_FLAGS={'active','controlConnected','mediaAllocated','localPeerSeen','udpSent','udpReceived'}
EVENTS={'warmup-ok','warmup-failed','tunnel-probe-failed','tunnel-probe-ok','core-restart','core-restart-failed',
    'media-reset','control-open','control-close','control-error','control-retry','media-startup-timeout',
    'media-transport-closed','media-path-timeout','media-path-changed','recovery-error','status-write-error','gateway-open','gateway-close','gateway-error','gateway-retry'}


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
        for name in ('controlConnectMs','discoveryMs','udpUpPackets','udpDownPackets','lastMediaDownAgeSeconds'):
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


def environment_snapshot():
    # Build distinguishes Windows 10 from 11: both report major version 10.
    # Deliberately exclude hostname, username, paths and device identifiers.
    result={'platform':sys.platform,'python':'.'.join(map(str,sys.version_info[:3]))}
    if sys.platform=='win32':
        version=sys.getwindowsversion()
        result['windows']={'major':version.major,'minor':version.minor,'build':version.build}
    return result


def save_report(base,installation):
    report={name:installation[name] for name in ('version','vencordFound','integrationInstalled','configurationPresent','corePresent') if name in installation}
    report['runtime']=runtime_snapshot(base)
    report['environment']=environment_snapshot()
    report['playback']=playback_snapshot(base)
    destination=Path(base)/'diagnostico.json'
    destination.parent.mkdir(parents=True,exist_ok=True)
    temporary=destination.with_suffix('.tmp')
    temporary.write_text(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    temporary.replace(destination)
    return str(destination)


def playback_snapshot(base):
    try:
        file=Path(base)/'playback-status.json'
        if file.stat().st_size>16384:return {'available':False}
        value=json.loads(file.read_text(encoding='utf-8'))
        if not isinstance(value,dict):return {'available':False}
    except (OSError,ValueError):return {'available':False}
    updated=value.get('updatedAt')
    result={'available':True,'stale':not number(updated) or not 0<=time.time()-updated<=20}
    for name in ('updatedAt','attempts'):
        if number(value.get(name)):result[name]=value[name]
    if value.get('lastReason') in ('first-frame-timeout','video-decode-stalled'):result['lastReason']=value['lastReason']
    result['streams']=[]
    streams=value.get('streams')
    for stream in streams[:8] if isinstance(streams,list) else []:
        if not isinstance(stream,dict):continue
        clean={}
        for name in ('sender','requested'):
            if type(stream.get(name)) is bool:clean[name]=stream[name]
        for name in ('frames','packets','packetsLost','decodeErrors','decryptFailures','recoveryAttempts'):
            if number(stream.get(name)):clean[name]=stream[name]
        if stream.get('state') in ('RTC_CONNECTED','RTC_CONNECTING','RTC_DISCONNECTED','NO_ROUTE','AWAITING_ENDPOINT','OTHER'):clean['state']=stream['state']
        result['streams'].append(clean)
    return result
