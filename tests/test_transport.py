import asyncio
import base64
from contextlib import asynccontextmanager
import json
import socket
import struct
import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.parse import urlencode

from websockets.asyncio.client import connect
from websockets.asyncio.server import serve
from websockets.exceptions import InvalidStatus,ConnectionClosed

from configuration import make_core_config
from socks_udp import SocksUDP,PacketQueue,pack_packet,unpack_packet
from stream_relay import MediaBridge,Relay,StreamSession,ready_target,sfu_target,request_details,valid_target

class DatagramEcho(asyncio.DatagramProtocol):
    def connection_made(self,transport): self.transport=transport
    def datagram_received(self,data,addr): self.transport.sendto(data,addr)

class FakeCore:
    running=True
    process=None
    async def ensure(self): pass
    async def stop(self): self.running=False

class FakeUpstream:
    def __init__(self,frames):
        self.queue=asyncio.Queue();self.received=[]
        for frame in frames: self.queue.put_nowait(frame)
    def __aiter__(self): return self
    async def __anext__(self): return await self.queue.get()
    async def send(self,value): self.received.append(value)

class FakeMedia:
    local_port=54321
    closed=False
    async def close(self): self.closed=True

class TransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_fresh_ready_replaces_media_but_resume_keeps_it(self):
        for version in (9,10):
            with self.subTest(version=version):
                endpoint={'ip':'8.8.8.8','port':50000}
                frames=([{'op':2,'d':endpoint}] if version==9 else
                        [{'op':2,'d':{'ssrc':123}},{'op':33,'d':{'primary':endpoint}}])
                media=[]
                async def factory(settings,target,stats):
                    bridge=FakeMedia();bridge.local_port=54321+len(media);media.append(bridge);return bridge
                relay=Relay({'nonce':'synthetic','socksPort':19885},core=FakeCore(),media_factory=factory)
                session=StreamSession(f'wss://test.discord.media/?v={version}')
                async def pump(messages):
                    upstream=FakeUpstream([json.dumps(frame) for frame in messages]);client=FakeUpstream([])
                    task=asyncio.create_task(relay.pump_down(upstream,client,session))
                    async def received():
                        while len(client.received)<len(messages):
                            if task.done():task.result()
                            await asyncio.sleep(.001)
                    try:
                        await asyncio.wait_for(received(),2)
                        return [json.loads(frame) for frame in client.received]
                    finally:
                        task.cancel();await asyncio.gather(task,return_exceptions=True)
                try:
                    await pump(frames)
                    first=session.media
                    # Buffered resume must preserve the local UDP port / peer.
                    await pump([{'op':9,'d':None}])
                    self.assertIs(session.media,first);self.assertFalse(first.closed)
                    if version==10:
                        await pump(frames[:1])
                        self.assertTrue(first.closed);self.assertIsNone(session.media)
                        result=await pump(frames[1:])
                        port=result[0]['d']['primary']['port']
                    else:
                        result=await pump(frames)
                        port=result[0]['d']['port']
                    self.assertTrue(first.closed);self.assertIsNot(session.media,first)
                    self.assertNotEqual(port,first.local_port)
                    self.assertEqual(relay.stats['mediaResets'],1)
                finally:
                    if session.media:await session.media.close()
                    await relay.close()

    async def test_normal_upstream_close_is_forwarded_without_waiting_for_heartbeat(self):
        class ClosedUpstream(FakeUpstream):
            close_code=1001
            async def __anext__(self):raise StopAsyncIteration
        @asynccontextmanager
        async def connector(target,**kwargs):
            yield ClosedUpstream([])
        relay=Relay({'nonce':'synthetic','socksPort':19885},core=FakeCore(),connector=connector)
        try:
            async with serve(relay.handle,'127.0.0.1',0,compression=None) as server:
                port=server.sockets[0].getsockname()[1]
                url=f'ws://127.0.0.1:{port}/relay?'+urlencode({'nonce':'synthetic','target':'wss://test.discord.media/?v=9','sid':'a'*32})
                async with connect(url,proxy=None) as client:
                    with self.assertRaises(ConnectionClosed) as error:
                        await asyncio.wait_for(client.recv(),2)
                    self.assertEqual(error.exception.rcvd.code,1001)
        finally:await relay.close()

    async def test_association_eof_requests_fresh_identification(self):
        reader=asyncio.StreamReader()
        socks=SocksUDP(reader,None,None,None,None)
        watcher=asyncio.create_task(socks.watch_control());reader.feed_eof()
        await asyncio.sleep(0)
        self.assertTrue(watcher.done())
        self.assertIsInstance(watcher.exception(),ConnectionError)
        closes=[]
        class Client:
            async def close(self,code):closes.append(code)
        class Bridge:
            tasks=[watcher]
            async def close(self):pass
        core=FakeCore();core.process=SimpleNamespace(pid=1)
        relay=Relay({'idleSeconds':40},core=core)
        session=StreamSession('wss://test.discord.media/?v=9')
        session.active=True;session.media=Bridge();session.client=Client()
        relay.sessions['a'*32]=session;relay.stats['activeControls']=1
        with tempfile.TemporaryDirectory() as directory,patch('stream_relay.BASE',Path(directory)):
            task=asyncio.create_task(relay.maintain())
            async def closed():
                while not closes:
                    if task.done():task.result()
                    await asyncio.sleep(.001)
            try:
                await asyncio.wait_for(closed(),2)
                self.assertEqual(closes,[4006])
                self.assertEqual(relay.stats['lastErrorClass'],'MediaTransportClosed')
            finally:
                task.cancel();await asyncio.gather(task,return_exceptions=True)
                await relay.close()

    def test_receive_queue_loss_is_counted(self):
        protocol=PacketQueue();stats={};protocol.stats=stats
        for _ in range(1027):protocol.datagram_received(b'packet',('127.0.0.1',1))
        self.assertEqual(protocol.drops,3)
        self.assertEqual(stats['udpReceiveQueueDrops'],3)

    def test_configuration_no_global_interface_or_dns(self):
        key=base64.b64encode(bytes(range(32))).decode()
        source=f'[Interface]\nPrivateKey={key}\nAddress=10.2.0.2/32\nDNS=10.2.0.1\n[Peer]\nPublicKey={key}\nAllowedIPs=0.0.0.0/0,::/0\nEndpoint=1.1.1.1:51820\n'
        config=make_core_config(source,{'socksPort':19885,'nonce':'synthetic'})
        self.assertFalse(config['endpoints'][0]['system'])
        self.assertEqual(config['inbounds'][0]['listen'],'127.0.0.1')
        self.assertEqual(config['dns']['servers'][0]['detour'],'proton')
        self.assertNotIn('listen',config['dns']['servers'][0])
        self.assertFalse(any(x['type']=='tun' for x in config['inbounds']))
        self.assertEqual(config['route']['final'],'proton')

    def test_restricted_control_targets_and_ready(self):
        for port in ('','2087','8443'):
            suffix=':'+port if port else ''
            self.assertTrue(valid_target('wss://sfu-test.discord.media'+suffix+'/?v=9'))
        for target in ('wss://127.0.0.1/?v=9','wss://x.discord.media.evil.test/?v=9','wss://x.discord.media:22/?v=9','wss://secret@x.discord.media/?v=9'):
            self.assertFalse(valid_target(target))
        self.assertIsNone(ready_target(b'\x00\xff\x01'))
        for host in ('127.0.0.1','10.0.0.1','169.254.169.254'):
            with self.assertRaises(ValueError): ready_target(json.dumps({'op':2,'d':{'ip':host,'port':50000}}))
        with self.assertRaises(ValueError): unpack_packet(b'\0\0\1\1'+b'\0'*6)

    def test_v10_ready_without_endpoint_waits_for_sfu_update(self):
        self.assertIsNone(ready_target(json.dumps({'op':2,'seq':1,'d':{'ssrc':123,'streams':[]}})))

    def test_v10_sfu_updates_keep_media_destination_restrictions(self):
        for address,port in [('127.0.0.1',50000),('10.0.0.1',50000),('169.254.169.254',50000),('8.8.8.8',22),('8.8.8.8',True)]:
            with self.subTest(address=address,port=port),self.assertRaises(ValueError):
                sfu_target(json.dumps({'op':33,'d':{'primary':{'ip':address,'port':port}}}))
        with self.assertRaises(ValueError):ready_target(json.dumps({'op':2,'d':{'ip':'8.8.8.8'}}))

    async def test_v10_sfu_endpoint_is_rewritten_and_binary_frames_are_unchanged(self):
        ready={'op':2,'seq':1,'d':{'ssrc':123,'streams':[]}}
        endpoint={'op':33,'seq':2,'d':{'primary':{'ip':'8.8.8.8','port':50000,'modes':['synthetic']}}}
        cleared={'op':33,'seq':3,'d':{'primary':None}}
        binary=b'\x00\x01\x1e\x00\xffsynthetic-mls'
        media=[]
        async def factory(settings,target,stats):
            self.assertEqual(target,('8.8.8.8',50000))
            value=FakeMedia();media.append(value);return value
        relay=Relay({'nonce':'synthetic','socksPort':19885,'idleSeconds':40},core=FakeCore(),media_factory=factory)
        upstream=FakeUpstream([json.dumps(ready),json.dumps(endpoint),binary,json.dumps(cleared)])
        client=FakeUpstream([])
        session=StreamSession('wss://test.discord.media/?v=10')
        task=asyncio.create_task(relay.pump_down(upstream,client,session))
        try:
            for _ in range(100):
                if len(client.received)==4 or task.done():break
                await asyncio.sleep(.001)
            if task.done():task.result()
            self.assertEqual(json.loads(client.received[0]),ready)
            result=json.loads(client.received[1])
            self.assertEqual(result['seq'],2)
            self.assertEqual(result['d']['primary'],{'ip':'127.0.0.1','port':54321,'modes':['synthetic']})
            self.assertEqual(client.received[2],binary)
            self.assertEqual(json.loads(client.received[3]),cleared)
            self.assertEqual(len(media),1)
            self.assertTrue(media[0].closed)
            self.assertIsNone(session.media)
        finally:
            task.cancel();await asyncio.gather(task,return_exceptions=True)
            await relay.close()

    async def test_sfu_retarget_keeps_same_endpoint_bridge_and_replaces_changed_endpoint(self):
        endpoints=[('8.8.8.8',50000),('8.8.8.8',50000),('1.1.1.1',50001)]
        upstream=FakeUpstream([json.dumps({'op':33,'seq':i,'d':{'primary':{'ip':host,'port':port}}})
                              for i,(host,port) in enumerate(endpoints)])
        media=[];targets=[]
        async def factory(settings,target,stats):
            targets.append(target);bridge=FakeMedia();bridge.local_port=54321+len(media);media.append(bridge);return bridge
        relay=Relay({'nonce':'synthetic','socksPort':19885},core=FakeCore(),media_factory=factory)
        client=FakeUpstream([]);session=StreamSession('wss://test.discord.media/?v=10')
        task=asyncio.create_task(relay.pump_down(upstream,client,session))
        try:
            for _ in range(100):
                if len(client.received)==3 or task.done():break
                await asyncio.sleep(.001)
            if task.done():task.result()
            self.assertEqual(targets,[endpoints[0],endpoints[2]])
            self.assertEqual([json.loads(frame)['d']['primary']['port'] for frame in client.received],[54321,54321,54322])
            self.assertTrue(media[0].closed);self.assertFalse(media[1].closed)
            self.assertEqual(session.media_target,endpoints[2])
        finally:
            task.cancel();await asyncio.gather(task,return_exceptions=True)
            if session.media:await session.media.close()
            await relay.close()

    async def test_authenticated_udp_association_and_native_datagrams(self):
        loop=asyncio.get_running_loop()
        echo,_=await loop.create_datagram_endpoint(DatagramEcho,local_addr=('127.0.0.1',0))
        udp_port=echo.get_extra_info('sockname')[1]
        errors=[]
        async def socks(reader,writer):
            try:
                self.assertEqual(await reader.readexactly(3),b'\5\1\2')
                writer.write(b'\5\2');await writer.drain()
                self.assertEqual(await reader.readexactly(1),b'\1')
                user=await reader.readexactly((await reader.readexactly(1))[0])
                password=await reader.readexactly((await reader.readexactly(1))[0])
                self.assertEqual(user,b'discord-stream');self.assertEqual(password,b'synthetic')
                writer.write(b'\1\0');await writer.drain()
                request=await reader.readexactly(10)
                self.assertEqual(request[:4],b'\5\3\0\1')
                writer.write(b'\5\0\0\1'+socket.inet_aton('127.0.0.1')+struct.pack('!H',udp_port));await writer.drain()
                await reader.read()
            except Exception as error: errors.append(error)
            finally: writer.close()
        server=await asyncio.start_server(socks,'127.0.0.1',0)
        stats={'udpQueueDrops':0,'udpUpBytes':0,'udpDownBytes':0,'udpUpPackets':0,'udpDownPackets':0}
        bridge=None;new_client=None;relay=None
        client,client_protocol=await loop.create_datagram_endpoint(PacketQueue,local_addr=('127.0.0.1',0))
        try:
            bridge=await MediaBridge.create({'socksPort':server.sockets[0].getsockname()[1],'nonce':'synthetic'},('8.8.8.8',50000),stats)
            for payload in (b'\0\1\0H'+bytes(range(70)),b'\x80\x60\x00\xff'+b'encrypted-placeholder'*40):
                client.sendto(payload,('127.0.0.1',bridge.local_port))
                received,_=await asyncio.wait_for(client_protocol.queue.get(),2)
                self.assertEqual(received,payload)
            self.assertEqual(stats['udpUpPackets'],2);self.assertEqual(stats['udpDownPackets'],2)
            self.assertEqual(stats['udpUpBytes'],stats['udpDownBytes'])
            # Malformed SOCKS input must not kill the receive task.
            bridge.socks.protocol.datagram_received(b'bad-socks-packet',bridge.socks.relay)
            client.sendto(b'after-malformed',('127.0.0.1',bridge.local_port))
            self.assertEqual((await asyncio.wait_for(client_protocol.queue.get(),2))[0],b'after-malformed')
            self.assertEqual(stats['udpMalformedPackets'],1)
            # Re-identify to the same SFU with a new native Discord UDP socket.
            # It must receive replies through a fresh bridge, not the old peer.
            settings={'socksPort':server.sockets[0].getsockname()[1],'nonce':'synthetic'}
            relay=Relay(settings,core=FakeCore())
            session=StreamSession('wss://test.discord.media/?v=9')
            session.media=bridge;session.media_target=('8.8.8.8',50000)
            relay.sessions['a'*32]=session
            upstream=FakeUpstream([json.dumps({'op':2,'d':{'ip':'8.8.8.8','port':50000}})])
            control=FakeUpstream([])
            task=asyncio.create_task(relay.pump_down(upstream,control,session))
            async def ready():
                while not control.received:
                    if task.done():task.result()
                    await asyncio.sleep(.001)
            try:
                await asyncio.wait_for(ready(),2)
            finally:
                task.cancel();await asyncio.gather(task,return_exceptions=True)
            endpoint=json.loads(control.received[0])['d']
            new_client,new_protocol=await loop.create_datagram_endpoint(PacketQueue,local_addr=('127.0.0.1',0))
            self.assertIsNot(session.media,bridge)
            new_client.sendto(b'new-discord-socket',('127.0.0.1',endpoint['port']))
            self.assertEqual((await asyncio.wait_for(new_protocol.queue.get(),2))[0],b'new-discord-socket')
        finally:
            client.close()
            if new_client:new_client.close()
            if relay:await relay.close()
            if bridge: await bridge.close()
            server.close();await server.wait_closed();echo.close()
        self.assertFalse(errors)

    async def test_control_ready_rewrite_binary_integrity_auth_and_resume(self):
        original={'op':2,'d':{'ip':'8.8.8.8','port':50000,'ssrc':123,'modes':['synthetic']}}
        binary=b'\x00\x01\x1e\x00\xffsynthetic-mls'
        upstreams=[];media=[]
        @asynccontextmanager
        async def connector(target,**kwargs):
            upstream=FakeUpstream([json.dumps(original),binary] if not upstreams else [binary])
            upstreams.append(upstream);yield upstream
        async def factory(settings,target,stats):
            self.assertEqual(target,('8.8.8.8',50000));value=FakeMedia();media.append(value);return value
        settings={'nonce':'synthetic','socksPort':19885,'idleSeconds':40}
        relay=Relay(settings,core=FakeCore(),connector=connector,media_factory=factory)
        try:
            async with serve(relay.handle,'127.0.0.1',0,process_request=relay.validate,origins=['https://discord.com'],compression=None) as server:
                port=server.sockets[0].getsockname()[1]
                def url(nonce='synthetic',target='wss://sfu-test.discord.media:8443/?v=9'):
                    return f'ws://127.0.0.1:{port}/relay?'+urlencode({'nonce':nonce,'target':target,'sid':'a'*32})
                for address,origin,status in ((url('incorrect'),'https://discord.com',401),(url(target='wss://example.com/?v=9'),'https://discord.com',400),(url(),'https://bad.example',403)):
                    with self.assertRaises(InvalidStatus) as error:
                        async with connect(address,origin=origin,proxy=None): pass
                    self.assertEqual(error.exception.response.status_code,status)
                async with connect(url(),origin='https://discord.com',proxy=None) as client:
                    ready=json.loads(await asyncio.wait_for(client.recv(),2))
                    self.assertEqual(ready['d']['ip'],'127.0.0.1');self.assertEqual(ready['d']['port'],54321)
                    self.assertEqual(ready['d']['ssrc'],123)
                    self.assertEqual(await client.recv(),binary)
                    await client.send('synthetic-control');await client.send(binary)
                    await asyncio.sleep(.02)
                await asyncio.sleep(.03)
                self.assertFalse(media[0].closed)
                async with connect(url(),origin='https://discord.com',proxy=None) as client:
                    self.assertEqual(await client.recv(),binary)
                self.assertEqual(len(media),1)
                self.assertEqual(upstreams[0].received,['synthetic-control',binary])
        finally: await relay.close()
        self.assertTrue(media[0].closed)

if __name__=='__main__': unittest.main()
