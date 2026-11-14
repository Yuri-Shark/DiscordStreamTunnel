"""The Windows worker can forward UDP and WebSocket controls on its selected loop."""
import asyncio
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

from websockets.asyncio.client import connect
from websockets.asyncio.server import serve

from socks_udp import PacketQueue,create_loopback_endpoint
from stream_relay import Relay,run_worker


@unittest.skipUnless(os.name=='nt','Windows worker event loop')
class WorkerLoopTests(unittest.TestCase):
    def run_probe(self,compat=False,fail=False):
        seen=[];policy=asyncio.get_event_loop_policy()
        async def probe(owner,idle):
            loop=asyncio.get_running_loop();seen.append(loop)
            self.assertIsInstance(loop,asyncio.SelectorEventLoop)
            self.assertEqual((owner,idle),(123,True))
            relay=Relay({})
            self.assertEqual(relay.health()['eventLoop'],'selector')
            sender,_=await create_loopback_endpoint(PacketQueue)
            receiver,incoming=await create_loopback_endpoint(PacketQueue)
            async def echo(socket):
                async for frame in socket:await socket.send(frame)
            try:
                async with serve(echo,'127.0.0.1',0) as server:
                    async with connect('ws://127.0.0.1:'+str(server.sockets[0].getsockname()[1]),proxy=None) as client:
                        for index in range(25):
                            payload=index.to_bytes(4,'big')+bytes(1196)
                            sender.sendto(payload,receiver.get_extra_info('sockname'))
                            self.assertEqual((await asyncio.wait_for(incoming.queue.get(),2))[0],payload)
                            await client.send(payload)
                            self.assertEqual(await asyncio.wait_for(client.recv(),2),payload)
                if fail:raise RuntimeError('synthetic worker failure')
                return 'complete'
            finally:sender.close();receiver.close()
        with patch('stream_relay.main',side_effect=probe):
            if compat:
                with patch('stream_relay.asyncio.Runner',None,create=True):
                    if fail:
                        with self.assertRaisesRegex(RuntimeError,'synthetic worker failure'):run_worker(123,True)
                    else:self.assertEqual(run_worker(123,True),'complete')
            else:self.assertEqual(run_worker(123,True),'complete')
        self.assertTrue(seen[0].is_closed())
        self.assertIs(asyncio.get_event_loop_policy(),policy)

    def test_worker_forwards_udp_and_controls_and_closes_loop(self):self.run_probe()
    def test_python_310_compatibility_restores_policy(self):self.run_probe(compat=True)
    def test_python_310_failure_also_restores_policy(self):self.run_probe(compat=True,fail=True)

    def test_application_entry_starts_worker_with_selector(self):
        root=Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            data=Path(directory)/'data';data.mkdir()
            with socket.socket() as reserved:
                reserved.bind(('127.0.0.1',0));port=reserved.getsockname()[1]
            settings={'relayPort':port,'socksPort':port,'nonce':'synthetic-entry-test','idleSeconds':40}
            (data/'local-settings.json').write_text(json.dumps(settings),encoding='utf-8')
            environment={**os.environ,'DTS_ROOT':directory,'PYTHONDONTWRITEBYTECODE':'1'}
            environment.pop('DISCORD_STREAM_TUNNEL_DATA',None)
            process=subprocess.Popen([sys.executable,str(root/'source/entry.py'),'--root',directory,'worker','--idle-exit'],
                cwd=root,env=environment,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            try:
                health=None;deadline=time.monotonic()+8
                while time.monotonic()<deadline and process.poll() is None:
                    request=urllib.request.Request(f'http://127.0.0.1:{port}/health?nonce=synthetic-entry-test',headers={'Origin':'https://discord.com'})
                    try:
                        with urllib.request.urlopen(request,timeout=.5) as response:health=json.load(response)
                    except (OSError,urllib.error.URLError):time.sleep(.05)
                    else:break
                self.assertIsNotNone(health,'the real application entry must start its HTTP/UDP worker')
                self.assertEqual(health['eventLoop'],'selector')
                self.assertFalse(health['configurationReady'],'the smoke test must not use a real VPN configuration')
                self.assertFalse(health['coreRunning'])
            finally:
                if process.poll() is None:process.terminate()
                process.wait(timeout=5)
