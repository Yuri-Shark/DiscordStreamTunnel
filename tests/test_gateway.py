import asyncio
from contextlib import asynccontextmanager
import json
from types import SimpleNamespace
import unittest
from urllib.parse import urlencode

from websockets.asyncio.client import connect
from websockets.asyncio.server import serve
from websockets.exceptions import InvalidStatus,ConnectionClosed

from stream_relay import Relay,valid_gateway_target,gateway_details


TARGET='wss://gateway-us-east1-b.discord.gg/?encoding=etf&v=9&compress=zstd-stream'


class Core:
    running=True
    process=SimpleNamespace(pid=1)
    async def ensure(self):pass
    async def stop(self):pass


class GatewayTests(unittest.IsolatedAsyncioTestCase):
    def test_restricted_gateway_targets_and_local_authorization(self):
        for target in (TARGET,'wss://gateway.discord.gg/?v=10&encoding=json','wss://gateway.discord.gg:443/?v=9&encoding=json&compress=zlib-stream'):
            self.assertTrue(valid_gateway_target(target))
        for target in ('wss://evil.example/?v=9&encoding=etf',TARGET+'&token=unexpected',TARGET+'&v=9',
                TARGET.replace('wss:','ws:'),TARGET.replace('etf','unknown'),TARGET.replace('v=9','v=8'),
                TARGET.replace('.gg/','.gg:8443/'),TARGET.replace('.gg/','.gg/not-allowed'),
                TARGET.replace('gateway-us-east1-b','voice'),TARGET.replace('gateway-us-east1-b.discord.gg','test.discord.media'),
                TARGET+'#fragment',TARGET.replace('wss://','wss://user:secret@'),TARGET.replace('v=9','v=')):
            self.assertFalse(valid_gateway_target(target),target)
        request='/gateway?'+urlencode({'nonce':'synthetic','target':TARGET})
        self.assertEqual(gateway_details(request,'synthetic'),TARGET)
        with self.assertRaises(PermissionError):gateway_details(request,'wrong')
        with self.assertRaises(ValueError):gateway_details(request+'&extra=unsafe','synthetic')

    async def test_binary_and_text_frames_forward_once_with_separate_limits(self):
        received=[]
        async def echo(peer):
            async for frame in peer:
                received.append(frame);await peer.send(frame)
        async with serve(echo,'127.0.0.1',0,max_size=8*1024*1024) as remote:
            url='ws://127.0.0.1:'+str(remote.sockets[0].getsockname()[1])
            @asynccontextmanager
            async def connector(target,**options):
                self.assertTrue(options['proxy'].startswith('socks5h://discord-stream:'))
                self.assertEqual(options['max_size'],8*1024*1024 if target==TARGET else 1048576)
                async with connect(url,proxy=None,max_size=8*1024*1024) as upstream:yield upstream
            relay=Relay({'nonce':'synthetic','socksPort':19885,'idleSeconds':40},core=Core(),connector=connector)
            try:
                async with serve(relay.handle,'127.0.0.1',0,process_request=relay.validate,max_size=8*1024*1024,max_queue=4) as local:
                    request='ws://127.0.0.1:'+str(local.sockets[0].getsockname()[1])+'/gateway?'+urlencode({'nonce':'synthetic','target':TARGET})
                    async with connect(request,proxy=None,origin='https://discord.com',max_size=8*1024*1024) as client:
                        frames=[json.dumps({'op':6,'d':{'token':'synthetic-not-a-credential','seq':123}}),b'\x83'+b'X'*1_100_000,b'\0\1\2']
                        for frame in frames:
                            await client.send(frame);self.assertEqual(await asyncio.wait_for(client.recv(),2),frame)
                        self.assertEqual(received,frames)
                        self.assertEqual(relay.stats['activeGatewayControls'],1)
                        self.assertEqual(relay.stats['activeControls'],0)
                        self.assertEqual(relay.sessions,{})
                        self.assertFalse(relay.healthy_streams(),'Gateway traffic is not proof of healthy stream UDP')
                        self.assertNotIn('synthetic-not-a-credential',json.dumps(relay.events))
                    with self.assertRaises(InvalidStatus):
                        async with connect(request,proxy=None,origin='https://untrusted.example'):pass
                    media='ws://127.0.0.1:'+str(local.sockets[0].getsockname()[1])+'/relay?'+urlencode({'nonce':'synthetic','target':'wss://test.discord.media/?v=9','sid':'a'*32})
                    async with connect(media,proxy=None,origin='https://discord.com') as client:
                        await client.send(b'X'*1_100_000)
                        with self.assertRaises(ConnectionClosed) as closed:await client.recv()
                        self.assertEqual(closed.exception.rcvd.code,1009)
                    self.assertEqual(received,frames,'oversized media control must not be forwarded')
            finally:await relay.close()

    async def test_gateway_limit_and_upstream_close_code_are_preserved(self):
        closed_codes=[]
        async def remote_handler(peer):
            await peer.send('ready')
            try:
                async for frame in peer:
                    if frame=='close':await peer.close(4004);return
            except ConnectionClosed:pass
            finally:closed_codes.append(peer.close_code)
        async with serve(remote_handler,'127.0.0.1',0) as remote:
            url='ws://127.0.0.1:'+str(remote.sockets[0].getsockname()[1])
            @asynccontextmanager
            async def connector(target,**options):
                async with connect(url,proxy=None) as upstream:yield upstream
            relay=Relay({'nonce':'synthetic','socksPort':19885,'idleSeconds':40},core=Core(),connector=connector)
            try:
                async with serve(relay.handle,'127.0.0.1',0,process_request=relay.validate) as server:
                    request='ws://127.0.0.1:'+str(server.sockets[0].getsockname()[1])+'/gateway?'+urlencode({'nonce':'synthetic','target':TARGET})
                    async with connect(request,proxy=None,origin='https://discord.com') as first,connect(request,proxy=None,origin='https://discord.com') as second:
                        self.assertEqual(await first.recv(),'ready');self.assertEqual(await second.recv(),'ready')
                        with self.assertRaises(InvalidStatus):
                            async with connect(request,proxy=None,origin='https://discord.com'):pass
                        await first.send('close')
                        with self.assertRaises(ConnectionClosed) as closed:await first.recv()
                        self.assertEqual(closed.exception.rcvd.code,4004)
                        await second.close(4000)
                        for _ in range(100):
                            if 4000 in closed_codes:break
                            await asyncio.sleep(.01)
                        self.assertIn(4000,closed_codes,'a client Resume close must reach Discord before context cleanup can send 1000')
            finally:await relay.close()
