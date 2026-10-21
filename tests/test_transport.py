import asyncio
import base64
from contextlib import asynccontextmanager
import json
import socket
import struct
import unittest
from urllib.parse import urlencode

from websockets.asyncio.client import connect
from websockets.asyncio.server import serve
from websockets.exceptions import InvalidStatus

from configuration import make_core_config
from socks_udp import SocksUDP,PacketQueue,pack_packet,unpack_packet
from stream_relay import MediaBridge,Relay,ready_target,request_details,valid_target

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
        bridge=None;client,client_protocol=await loop.create_datagram_endpoint(PacketQueue,local_addr=('127.0.0.1',0))
        try:
            bridge=await MediaBridge.create({'socksPort':server.sockets[0].getsockname()[1],'nonce':'synthetic'},('8.8.8.8',50000),stats)
            for payload in (b'\0\1\0H'+bytes(range(70)),b'\x80\x60\x00\xff'+b'encrypted-placeholder'*40):
                client.sendto(payload,('127.0.0.1',bridge.local_port))
                received,_=await asyncio.wait_for(client_protocol.queue.get(),2)
                self.assertEqual(received,payload)
            self.assertEqual(stats['udpUpPackets'],2);self.assertEqual(stats['udpDownPackets'],2)
            self.assertEqual(stats['udpUpBytes'],stats['udpDownBytes'])
        finally:
            client.close()
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
