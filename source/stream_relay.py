"""Discord stream-only control and encrypted UDP relay through userspace WireGuard.

Never changes system routes/DNS. Never logs payloads, tokens or media keys.
"""
import asyncio
from contextlib import AsyncExitStack,suppress
import hmac
import ipaddress
import json
import logging
import os
from pathlib import Path
import re
import subprocess
import secrets
import struct
import time
from urllib.parse import parse_qs,urlsplit,quote

from websockets.asyncio.client import connect
from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosed,ProxyError

from configuration import BASE,CORE,local_settings
from socks_udp import SocksUDP

HOST=re.compile(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.discord\.media\Z')
VERSION=json.loads((Path(__file__).resolve().parent/'version.json').read_text())['version']
SID=re.compile(r'[a-f0-9]{32}\Z')
class MissingConfiguration(Exception): pass
class TunnelRestarted(ConnectionError): pass
class MediaStartupTimeout(ConnectionError): pass

def valid_target(target):
    try:
        u=urlsplit(target)
        return (u.scheme=='wss' and bool(HOST.fullmatch(u.hostname or '')) and u.port in (None,443,2053,2083,2087,2096,8443)
            and u.path in ('','/') and not u.username and not u.password and not u.fragment
            and parse_qs(u.query,strict_parsing=True) in ({'v':['8']},{'v':['9']},{'v':['10']}))
    except (ValueError,TypeError): return False

def request_details(path,nonce):
    u=urlsplit(path);q=parse_qs(u.query,keep_blank_values=True)
    if u.path!='/relay' or set(q)!={'nonce','target','sid'} or any(len(v)!=1 for v in q.values()): raise ValueError('Invalid relay request')
    if not hmac.compare_digest(q['nonce'][0],nonce): raise PermissionError('Invalid local authorization')
    if not valid_target(q['target'][0]) or not SID.fullmatch(q['sid'][0]): raise ValueError('Invalid target or local session')
    return q['target'][0],q['sid'][0]

def media_target(body):
    if not isinstance(body,dict): raise ValueError('Invalid media endpoint')
    address=ipaddress.ip_address(body.get('ip',''))
    port=body.get('port')
    if not address.is_global or type(port) is not int or not 1024<=port<=65535: raise ValueError('Invalid media destination')
    return str(address),port

def media_message(message,opcode):
    if not isinstance(message,str): return None
    try: data=json.loads(message)
    except (ValueError,TypeError): return None
    if not isinstance(data,dict) or data.get('op')!=opcode: return None
    if not isinstance(data.get('d'),dict): raise ValueError('Invalid media control message')
    return data

def ready_target(message):
    data=media_message(message,2)
    if data is None:return None
    body=data['d']
    # Voice gateway v10 may send READY before assigning an SFU endpoint.
    if body.get('ip') is None and body.get('port') is None:return None
    return data,media_target(body)

def sfu_target(message):
    data=media_message(message,33)
    if data is None:return None
    primary=data['d'].get('primary')
    return data,None if primary is None else media_target(primary)

class Core:
    def __init__(self,settings,base=BASE):
        self.settings,self.base,self.process,self.lock=settings,base,None,asyncio.Lock()
    @property
    def running(self): return self.process is not None and self.process.poll() is None
    async def ensure(self):
        async with self.lock:
            await self._ensure()
    async def probe(self):
        _,writer=await asyncio.wait_for(asyncio.open_connection('127.0.0.1',self.settings['socksPort']),1)
        writer.close()
        with suppress(OSError,TimeoutError,asyncio.TimeoutError):await asyncio.wait_for(writer.wait_closed(),1)
    async def _ensure(self):
        if self.running:
            try:await self.probe();return
            # A slow local SOCKS accept must not silently replace a core whose
            # UDP associations may still be serving other streams.
            except (OSError,TimeoutError,asyncio.TimeoutError):raise ConnectionError('CoreProxyUnavailable') from None
        config=self.base/'private-core.json'
        if not config.exists(): raise MissingConfiguration()
        environment={**os.environ,'GOMAXPROCS':'2','GOMEMLIMIT':'128MiB'}
        self.process=subprocess.Popen([str(CORE),'run','-c',str(config)],cwd=self.base,
            stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,env=environment,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        deadline=time.monotonic()+5
        while time.monotonic()<deadline:
            if not self.running: raise ConnectionError('WireGuard core exited')
            try:await self.probe();return
            except (OSError,TimeoutError,asyncio.TimeoutError):await asyncio.sleep(.05)
        await self._stop();raise TimeoutError('Local WireGuard core did not start')
    async def restart(self):
        async with self.lock:
            await self._stop();await self._ensure()
    async def stop(self):
        async with self.lock:await self._stop()
    async def _stop(self):
        if self.running:
            self.process.terminate()
            with suppress(subprocess.TimeoutExpired): await asyncio.to_thread(self.process.wait,3)
            if self.running:
                self.process.kill();await asyncio.to_thread(self.process.wait,3)
        self.process=None

class LocalDatagrams(asyncio.DatagramProtocol):
    def __init__(self,bridge): self.bridge=bridge
    def connection_made(self,transport): self.bridge.transport=transport
    def datagram_received(self,data,addr):
        b=self.bridge
        if addr[0]!='127.0.0.1' or len(data)>65507: return
        if b.peer is None: b.peer=addr
        if addr!=b.peer: return
        if len(data)==74 and data[:4]==b'\x00\x01\x00\x46' and b.discovery_packet is None:
            b.discovery_packet=data;b.discovery_started=time.monotonic();b.discovery_pending.set()
        try: b.queue.put_nowait(data)
        except asyncio.QueueFull: b.stats['udpQueueDrops']+=1

class MediaBridge:
    def __init__(self,socks,target,stats):
        self.socks,self.target,self.stats=socks,target,stats
        self.transport=None;self.peer=None;self.tasks=[];self.queue=asyncio.Queue(maxsize=512)
        self.first_up_at=None;self.first_down_at=None
        self.discovery_packet=None;self.discovery_started=None;self.discovery_ms=None
        self.discovery_pending=asyncio.Event();self.discovery_done=asyncio.Event()
        self.startup_timeout=8
    @classmethod
    async def create(cls,settings,target,stats):
        socks=await asyncio.wait_for(SocksUDP.create(settings['socksPort'],'discord-stream',settings['nonce']),5)
        socks.protocol.stats=stats
        bridge=cls(socks,target,stats)
        bridge.startup_timeout=settings.get('mediaStartupSeconds',8)
        try:
            await asyncio.get_running_loop().create_datagram_endpoint(lambda:LocalDatagrams(bridge),local_addr=('127.0.0.1',0))
            bridge.tasks=[asyncio.create_task(bridge.outbound()),asyncio.create_task(bridge.inbound()),
                          asyncio.create_task(socks.watch_control()),asyncio.create_task(bridge.discover())]
            return bridge
        except BaseException:
            await socks.close();raise
    @property
    def local_port(self): return self.transport.get_extra_info('sockname')[1]
    async def outbound(self):
        while True:
            data=await self.queue.get()
            await self.socks.send(self.target,data)
            if self.first_up_at is None:self.first_up_at=time.monotonic()
            self.stats['udpUpBytes']+=len(data);self.stats['udpUpPackets']+=1
    async def discover(self):
        # Only repeat Discord's unencrypted, idempotent IP discovery query.
        # Never replay encrypted media, DAVE frames or control messages.
        await self.discovery_pending.wait()
        for delay in (.25,.5,1):
            remaining=self.startup_timeout-(time.monotonic()-self.discovery_started)
            if remaining<=0:break
            try:await asyncio.wait_for(self.discovery_done.wait(),min(delay,remaining))
            except asyncio.TimeoutError:pass
            else:break
            if time.monotonic()-self.discovery_started>=self.startup_timeout:break
            await self.socks.send(self.target,self.discovery_packet)
            self.stats['udpDiscoveryRetries']=self.stats.get('udpDiscoveryRetries',0)+1
            self.stats['udpUpBytes']+=len(self.discovery_packet);self.stats['udpUpPackets']+=1
        remaining=max(0,self.startup_timeout-(time.monotonic()-self.discovery_started))
        try:await asyncio.wait_for(self.discovery_done.wait(),remaining)
        except asyncio.TimeoutError:raise MediaStartupTimeout('UDPDiscoveryTimeout') from None
        # Successful setup isn't a terminated transport task. Stay dormant;
        # silence in paused video must never trigger a reconnect.
        await asyncio.Future()
    async def inbound(self):
        while True:
            try: target,data=await self.socks.receive()
            except ValueError:
                self.stats['udpMalformedPackets']=self.stats.get('udpMalformedPackets',0)+1
                continue
            if target!=self.target or self.peer is None: continue
            self.transport.sendto(data,self.peer)
            if self.first_down_at is None:self.first_down_at=time.monotonic()
            if (self.discovery_packet is not None and len(data)==74 and data[:4]==b'\x00\x02\x00\x46'
                    and data[4:8]==self.discovery_packet[4:8] and not self.discovery_done.is_set()):
                self.discovery_ms=round((time.monotonic()-self.discovery_started)*1000);self.discovery_done.set()
            self.stats['udpDownBytes']+=len(data);self.stats['udpDownPackets']+=1
            self.stats['lastUdpDownAt']=time.time()
    async def close(self):
        for task in self.tasks: task.cancel()
        await asyncio.gather(*self.tasks,return_exceptions=True)
        if self.transport: self.transport.close()
        await self.socks.close()

class StreamSession:
    def __init__(self,target):
        self.target=target;self.media=None;self.media_target=None;self.active=False;self.connected=False;self.closed_at=time.monotonic()
        self.connected_at=None;self.control_ms=None;self.failure_handled=False

class Relay:
    def __init__(self,settings,core=None,connector=connect,media_factory=MediaBridge.create,owner_pid=None,clock=None):
        self.settings=settings;self.core=core or Core(settings);self.connector=connector;self.media_factory=media_factory
        self.sessions={};self.stopping=False
        self.stats={'openedControls':0,'activeControls':0,'controlUpBytes':0,'controlDownBytes':0,
            'udpUpBytes':0,'udpDownBytes':0,'udpUpPackets':0,'udpDownPackets':0,'udpQueueDrops':0,
            'udpReceiveQueueDrops':0,'udpMalformedPackets':0,'udpDiscoveryRetries':0,'controlRetries':0,
            'mediaStartupTimeouts':0,'mediaResets':0,'coreRestarts':0,'lastUdpDownAt':None,'lastErrorClass':None}
        self.events=[]
        self.last_control=time.monotonic()
        self.warm_lock=asyncio.Lock();self.warmed_at=None;self.warmup_ms=None
        self.owner_pid=owner_pid;self.generation=0;self.recovery_lock=asyncio.Lock()
        self.clock=clock or time.monotonic
        self.last_recovery_probe=None;self.last_restart=None;self.failed_probes=0
        self.tunnel_reachable=None;self.tunnel_checked_at=None
        self.pending_recovery=None
    def note(self,event,**details):
        self.events.append({'at':time.time(),'event':event,**details})
        del self.events[:-64]
    async def check_tunnel(self):
        await self.core.ensure();socks=None
        try:
            config=json.loads((BASE/'private-core.json').read_text())
            dns=config['dns']['servers'][0]['server'];transaction=secrets.token_bytes(2)
            query=transaction+b'\x01\x00\x00\x01\x00\x00\x00\x00\x00\x00'+b'\x07discord\x03com\x00\x00\x01\x00\x01'
            socks=await asyncio.wait_for(SocksUDP.create(self.settings['socksPort'],'discord-stream',self.settings['nonce']),3)
            for attempt in range(2):
                await socks.send((dns,53),query)
                deadline=time.monotonic()+2
                while time.monotonic()<deadline:
                    try:source,response=await asyncio.wait_for(socks.receive(),max(.001,deadline-time.monotonic()))
                    except ValueError:continue
                    except asyncio.TimeoutError:break
                    if source==(dns,53) and response[:2]==transaction and len(response)>=12 and response[2]&128:return
            raise TimeoutError('TunnelDNSProbeTimeout')
        finally:
            if socks:await socks.close()
    async def verify_tunnel(self):
        generation=self.generation
        try:await asyncio.wait_for(self.check_tunnel(),8)
        except Exception:
            if generation==self.generation:self.tunnel_reachable=False;self.tunnel_checked_at=time.time()
            raise
        if generation==self.generation:self.tunnel_reachable=True;self.tunnel_checked_at=time.time()
    async def warm(self):
        async with self.warm_lock:
            if self.core.running and self.warmed_at is not None and time.monotonic()-self.warmed_at<30:return
            async with self.recovery_lock:
                if self.stopping:return
                generation=self.generation
            # A background DNS timeout must not hold up a stream handshake.
            started=time.monotonic();await self.verify_tunnel()
            if generation!=self.generation or self.stopping:return
            self.warmed_at=time.monotonic();self.warmup_ms=round((self.warmed_at-started)*1000)
            self.failed_probes=0;self.note('warmup-ok',elapsedMs=self.warmup_ms)
    async def preheat(self):
        failures=0
        while not self.stopping:
            generation=self.generation
            try:await self.warm()
            except Exception as error:
                failures+=1;self.note('warmup-failed',errorClass=type(error).__name__)
                try:await self.recover_tunnel(generation)
                except Exception as recovery_error:self.note('recovery-error',errorClass=type(recovery_error).__name__)
            else:failures=0
            if self.stopping:return
            await asyncio.sleep(min(60,5*2**min(failures-1,4)) if failures else 30)
    def healthy_streams(self):
        for session in self.sessions.values():
            if not session.connected:continue
            # Give controls time to negotiate media. After that, a control
            # alone is insufficient evidence of a working UDP path.
            if session.connected_at is not None and self.clock()-session.connected_at<15:return True
            if session.media is not None and getattr(session.media,'first_down_at',None) is not None:return True
        return False
    async def recover_tunnel(self,generation):
        async with self.recovery_lock:
            now=self.clock()
            if self.stopping:return False
            if generation!=self.generation:return True
            # A failing stream must never restart a tunnel used by healthy ones.
            if self.healthy_streams():return False
            if self.last_recovery_probe is not None and now-self.last_recovery_probe<5:return False
            self.last_recovery_probe=now
            try:await self.verify_tunnel()
            except Exception as error:
                self.failed_probes+=1;self.note('tunnel-probe-failed',errorClass=type(error).__name__)
            else:
                self.failed_probes=0;self.note('tunnel-probe-ok');return False
            if self.failed_probes<2 or (self.last_restart is not None and self.clock()-self.last_restart<30):return False
            if self.healthy_streams():return False
            self.last_restart=self.clock();self.generation+=1;self.failed_probes=0;self.warmed_at=None
            self.stats['lastUdpDownAt']=None;self.tunnel_reachable=False;self.tunnel_checked_at=time.time()
            self.stats['coreRestarts']+=1;self.note('core-restart')
            # All bridges belong to the previous SOCKS process and cannot resume.
            for session in list(self.sessions.values()):
                if session.media:
                    previous=session.media;session.media=None;session.media_target=None
                    await previous.close()
            try:await asyncio.wait_for(self.core.restart(),10)
            except Exception as error:self.note('core-restart-failed',errorClass=type(error).__name__)
            return True
    def request_recovery(self):
        if self.stopping or self.pending_recovery is not None and not self.pending_recovery.done():return
        generation=self.generation
        async def run():
            try:await self.recover_tunnel(generation)
            except Exception as error:self.note('recovery-error',errorClass=type(error).__name__)
        self.pending_recovery=asyncio.create_task(run())
    def health(self):
        recent_media=self.stats['lastUdpDownAt'] is not None and time.time()-self.stats['lastUdpDownAt']<30
        reachable=True if recent_media else self.tunnel_reachable if self.tunnel_checked_at is not None and time.time()-self.tunnel_checked_at<60 else None
        return {'service':'DiscordStreamTunnel','protocolVersion':1,'pid':os.getpid(),'ownerPID':self.owner_pid,'configurationReady':(BASE/'private-core.json').exists(),'coreRunning':self.core.running,
            'version':VERSION,'buildRevision':'startup-20261028','warmupMs':self.warmup_ms,
            'keepWarm':bool(self.owner_pid),'lastWarmupAgeSeconds':None if self.warmed_at is None else round(time.monotonic()-self.warmed_at,1),
            'stopping':self.stopping,'coreGeneration':self.generation,
            'tunnelReachable':reachable,'lastTunnelCheckAt':self.tunnel_checked_at,
            'activeControls':self.stats['activeControls'],'mediaBridges':sum(x.media is not None for x in self.sessions.values()),
            'scope':'Discord screen-stream sessions only; no system routes or DNS'}
    def stream_diagnostics(self):
        # No URLs, session IDs, tokens, payloads, addresses or media keys.
        result=[]
        for session in self.sessions.values():
            media=session.media
            result.append({'active':session.active,'controlConnected':session.connected,'controlConnectMs':session.control_ms,
                'mediaAllocated':media is not None,'localPeerSeen':media is not None and getattr(media,'peer',None) is not None,
                'udpSent':media is not None and getattr(media,'first_up_at',None) is not None,
                'udpReceived':media is not None and getattr(media,'first_down_at',None) is not None,
                'discoveryMs':getattr(media,'discovery_ms',None)})
        return result
    async def validate(self,connection,request):
        if request.headers.get('Origin')!='https://discord.com': return connection.respond(403,'Forbidden')
        u=urlsplit(request.path);q=parse_qs(u.query,keep_blank_values=True)
        if u.path in ('/health','/warmup'):
            if set(q)!={'nonce'} or len(q['nonce'])!=1 or not hmac.compare_digest(q['nonce'][0],self.settings['nonce']): return connection.respond(401,'Unauthorized')
            if u.path=='/warmup':
                try:await self.warm()
                except Exception as error:self.stats['lastErrorClass']=type(error).__name__
            response=connection.respond(200,json.dumps(self.health()))
            response.headers['Access-Control-Allow-Origin']='https://discord.com'
            response.headers['Content-Type']='application/json'
            return response
        try: target,sid=request_details(request.path,self.settings['nonce'])
        except PermissionError: return connection.respond(401,'Unauthorized')
        except ValueError: return connection.respond(400,'Invalid request')
        if self.stats['activeControls']>=4 or (sid in self.sessions and self.sessions[sid].active): return connection.respond(409,'Receiver limit')
        if sid not in self.sessions and len(self.sessions)>=8:
            inactive=[(key,value) for key,value in self.sessions.items() if not value.active]
            if not inactive:return connection.respond(503,'Session limit')
            oldest,previous=min(inactive,key=lambda item:item[1].closed_at)
            self.sessions.pop(oldest)
            if previous.media:await previous.media.close()
    async def pump_up(self,client,upstream):
        async for frame in client:
            await upstream.send(frame)
            self.stats['controlUpBytes']+=len(frame.encode() if isinstance(frame,str) else frame)
    async def pump_down(self,upstream,client,session):
        async for frame in upstream:
            # A fresh READY invalidates old media even in v10, where the new
            # endpoint arrives later via SFU_UPDATE. Resume (op 9) preserves it.
            fresh=media_message(frame,2) is not None
            endpoint=ready_target(frame) or sfu_target(frame)
            if fresh and session.media is not None:
                previous=session.media;session.media=None;session.media_target=None
                await previous.close()
                self.stats['mediaResets']+=1;self.note('media-reset',opcode=2)
            if endpoint is not None:
                message,target=endpoint
                if session.media is not None and session.media_target!=target:
                    previous=session.media;session.media=None;session.media_target=None
                    await previous.close()
                    self.stats['mediaResets']+=1;self.note('media-reset',opcode=message['op'])
                if target is not None:
                    if session.media is None:
                        session.media=await self.media_factory(self.settings,target,self.stats)
                        session.media_target=target
                    body=message['d']['primary'] if message['op']==33 else message['d']
                    body['ip']='127.0.0.1';body['port']=session.media.local_port
                frame=json.dumps(message,separators=(',',':'))
            await client.send(frame)
            self.stats['controlDownBytes']+=len(frame.encode() if isinstance(frame,str) else frame)
    async def open_control(self,target,client,stack):
        # Retry only the handshake, before forwarding any application frame.
        # Once connected, Discord owns Identify/Resume and all heartbeat state.
        closed=asyncio.create_task(client.wait_closed())
        deadline=self.clock()+15
        try:
            for attempt in range(2):
                proxy=f"socks5h://discord-stream:{quote(self.settings['nonce'],safe='')}@127.0.0.1:{self.settings['socksPort']}"
                opening=asyncio.create_task(stack.enter_async_context(self.connector(target,proxy=proxy,origin='https://discord.com',
                    compression=None,ping_interval=None,open_timeout=max(.001,deadline-self.clock()),close_timeout=2,max_size=1048576,max_queue=16)))
                try:
                    done,_=await asyncio.wait((opening,closed),return_when=asyncio.FIRST_COMPLETED)
                    if closed in done:return None
                    return opening.result()
                except (ProxyError,TimeoutError,asyncio.TimeoutError,OSError) as error:
                    if attempt or deadline-self.clock()<=.25:raise
                    self.stats['controlRetries']+=1;self.note('control-retry',errorClass=type(error).__name__)
                    try:await asyncio.wait_for(asyncio.shield(closed),.25)
                    except asyncio.TimeoutError:pass
                    else:return None
                finally:
                    if not opening.done():opening.cancel()
                    await asyncio.gather(opening,return_exceptions=True)
        finally:
            closed.cancel();await asyncio.gather(closed,return_exceptions=True)
    async def handle(self,client):
        target,sid=request_details(client.request.path,self.settings['nonce'])
        session=self.sessions.setdefault(sid,StreamSession(target))
        if session.target!=target:
            if session.media: await session.media.close()
            session=StreamSession(target);self.sessions[sid]=session
        if session.active or self.stats['activeControls']>=4: await client.close(1013);return
        session.active=True;self.stats['activeControls']+=1
        session.client=client;session.failure_handled=False;session.connected_at=None;session.control_ms=None
        started=self.clock()
        generation=self.generation
        try:
            # No new control may attach to a core halfway through recovery.
            async with self.recovery_lock:
                if self.stopping:raise ConnectionError('RelayStopping')
                await self.core.ensure();generation=self.generation
            async with AsyncExitStack() as stack:
                upstream=await self.open_control(target,client,stack)
                if upstream is None:return
                if generation!=self.generation:raise TunnelRestarted()
                session.connected=True;session.connected_at=self.clock();session.control_ms=round((self.clock()-started)*1000)
                self.tunnel_reachable=True;self.tunnel_checked_at=time.time()
                self.stats['openedControls']+=1;self.stats['lastErrorClass']=None
                self.note('control-open',elapsedMs=session.control_ms)
                tasks=[asyncio.create_task(self.pump_up(client,upstream)),asyncio.create_task(self.pump_down(upstream,client,session))]
                try:
                    done,_=await asyncio.wait(tasks,return_when=asyncio.FIRST_COMPLETED)
                    for task in done: task.result()
                    # A normal upstream close ends iteration without raising.
                    # Propagate it so the client can reconnect immediately.
                    if tasks[1] in done:
                        code=getattr(upstream,'close_code',None) or 1000
                        await client.close(code=code if code in (1000,1001) or 3000<=code<=4999 else 1011)
                        self.note('control-close',code=code)
                finally:
                    for task in tasks: task.cancel()
                    await asyncio.gather(*tasks,return_exceptions=True)
        except ConnectionClosed as error:
            code=error.rcvd.code if error.rcvd else 1011
            self.note('control-close',code=code)
            if code not in (1000,1001) and not 3000<=code<=4999: code=1011
            with suppress(Exception): await client.close(code=code)
        except Exception as error:
            session.connected=False
            self.stats['lastErrorClass']=type(error).__name__
            self.note('control-error',errorClass=type(error).__name__)
            reset=generation!=self.generation
            if isinstance(error,(ProxyError,TimeoutError,asyncio.TimeoutError,OSError)):
                try:reset=await self.recover_tunnel(generation) or reset
                except Exception as recovery_error:self.note('recovery-error',errorClass=type(recovery_error).__name__)
            with suppress(Exception): await client.close(4006 if reset else 1011)
        finally:
            session.active=False;session.connected=False;session.closed_at=time.monotonic()
            self.stats['activeControls']-=1;self.last_control=time.monotonic()
    async def maintain(self):
        while not self.stopping:
            now=time.monotonic()
            for sid,session in list(self.sessions.items()):
                if session.active and session.media and any(task.done() for task in session.media.tasks):
                    if session.failure_handled:continue
                    session.failure_handled=True
                    errors=[task.exception() for task in session.media.tasks if task.done() and not task.cancelled()]
                    startup=any(isinstance(error,MediaStartupTimeout) for error in errors)
                    self.stats['lastErrorClass']='MediaStartupTimeout' if startup else 'MediaTransportClosed'
                    if startup:self.stats['mediaStartupTimeouts']+=1
                    self.note('media-startup-timeout' if startup else 'media-transport-closed')
                    previous=session.media;session.media=None;session.media_target=None
                    session.connected=False
                    await previous.close()
                    if startup:self.request_recovery()
                    # A broken association cannot be resumed. Ask for fresh
                    # identification / READY rather than reusing dead media.
                    with suppress(Exception):await session.client.close(4006)
                if not session.active and now-session.closed_at>self.settings['idleSeconds']:
                    if session.media: await session.media.close()
                    self.sessions.pop(sid,None)
            if not self.owner_pid and not self.sessions and not self.stats['activeControls'] and now-self.last_control>self.settings.get('warmIdleSeconds',120):
                await self.core.stop()
            record={'pid':os.getpid(),'corePID':self.core.process.pid if self.core.running else None,'updatedAt':time.time(),
                **self.health(),**self.stats,'streams':self.stream_diagnostics(),'recentEvents':self.events}
            try:
                temp=BASE/'status.tmp';temp.write_text(json.dumps(record,indent=2),encoding='utf-8');temp.replace(BASE/'status.json')
            except OSError as error:self.note('status-write-error',errorClass=type(error).__name__)
            await asyncio.sleep(2)
    async def close(self):
        self.stopping=True
        if self.pending_recovery:
            self.pending_recovery.cancel();await asyncio.gather(self.pending_recovery,return_exceptions=True)
            self.pending_recovery=None
        await self.core.stop()
        for session in list(self.sessions.values()):
            if session.media: await session.media.close()
        self.sessions.clear()

class OwnerProcess:
    def __init__(self,pid):
        self.handle=None;self.pid=pid;self.pidfd=None;self.identity=None
        if pid and os.name=='posix':
            if pid<=1:raise ProcessLookupError('Invalid owner process')
            if hasattr(os,'pidfd_open'):
                try:self.pidfd=os.pidfd_open(pid,0)
                except ProcessLookupError:raise
                except OSError:pass
            if self.pidfd is None:
                os.kill(pid,0)
                self.identity=self.proc_identity()
        if pid and os.name=='nt':
            import ctypes
            from ctypes import wintypes
            self.api=ctypes.WinDLL('kernel32',use_last_error=True)
            self.api.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD]
            self.api.OpenProcess.restype=wintypes.HANDLE
            self.api.WaitForSingleObject.argtypes=[wintypes.HANDLE,wintypes.DWORD]
            self.api.WaitForSingleObject.restype=wintypes.DWORD
            self.api.CloseHandle.argtypes=[wintypes.HANDLE]
            self.handle=self.api.OpenProcess(0x00100000,False,pid)
            if not self.handle: raise ProcessLookupError('Owner process unavailable')
    def proc_identity(self):
        try:return (Path('/proc')/str(self.pid)/'stat').read_text().rsplit(')',1)[1].split()[19]
        except (OSError,IndexError):return None
    def alive(self):
        if self.pidfd is not None:
            import select
            return not select.select([self.pidfd],[],[],0)[0]
        if self.pid and os.name=='posix':
            try:os.kill(self.pid,0)
            except ProcessLookupError:return False
            except PermissionError:pass
            return self.identity is None or self.identity==self.proc_identity()
        return self.handle is None or self.api.WaitForSingleObject(self.handle,0)==258
    def close(self):
        if self.handle: self.api.CloseHandle(self.handle);self.handle=None
        if self.pidfd is not None:os.close(self.pidfd);self.pidfd=None

async def main(owner_pid=None,idle_exit=False):
    settings=local_settings();relay=Relay(settings,owner_pid=owner_pid)
    owner=OwnerProcess(owner_pid)
    stop=asyncio.Event();signals=[]
    if os.name=='posix':
        import signal
        for signum in (signal.SIGTERM,signal.SIGINT):
            asyncio.get_running_loop().add_signal_handler(signum,stop.set);signals.append(signum)
    logging.getLogger('websockets').disabled=True
    try:
        async with serve(relay.handle,'127.0.0.1',settings['relayPort'],process_request=relay.validate,origins=['https://discord.com'],compression=None,ping_interval=None,max_size=1048576,max_queue=16):
            task=asyncio.create_task(relay.maintain())
            warmer=asyncio.create_task(relay.preheat()) if owner_pid else None
            try:
                while not stop.is_set() and owner.alive() and not task.done():
                    if idle_exit and not owner_pid and not relay.sessions and not relay.stats['activeControls'] and time.monotonic()-relay.last_control>settings.get('warmIdleSeconds',120)+4: break
                    await asyncio.sleep(2)
                if task.done(): task.result()
            finally:
                relay.stopping=True
                background=[task]+([warmer] if warmer else [])
                for running in background:running.cancel()
                await asyncio.gather(*background,return_exceptions=True)
                # Release the old core before waiting for pending upstream
                # handshakes while a new Discord instance is starting.
                await relay.close()
    finally:
        await relay.close();owner.close()
        for signum in signals:asyncio.get_running_loop().remove_signal_handler(signum)

if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--owner-pid',type=int);parser.add_argument('--idle-exit',action='store_true')
    args=parser.parse_args()
    try: asyncio.run(main(args.owner_pid,args.idle_exit))
    except KeyboardInterrupt: pass
