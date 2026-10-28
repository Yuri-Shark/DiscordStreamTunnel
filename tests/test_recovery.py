import asyncio
from contextlib import asynccontextmanager
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock,patch
from urllib.parse import urlencode

from websockets.asyncio.client import connect
from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosed,ProxyError
from stream_relay import Relay,StreamSession,main


class Core:
    running=True
    process=SimpleNamespace(pid=1)
    def __init__(self):self.restarts=0
    async def ensure(self):pass
    async def restart(self):self.restarts+=1
    async def stop(self):self.running=False


class RecoveryTests(unittest.IsolatedAsyncioTestCase):
    def make_relay(self):
        self.clock=[0]
        relay=Relay({'nonce':'synthetic','socksPort':19885,'idleSeconds':40},core=Core(),owner_pid=1234,clock=lambda:self.clock[0])
        relay.check_tunnel=AsyncMock(side_effect=TimeoutError)
        return relay

    async def test_repeated_failed_probes_restart_core_and_discard_old_media(self):
        relay=self.make_relay();media=SimpleNamespace(close=AsyncMock())
        session=StreamSession('wss://test.discord.media/?v=9');session.media=media
        relay.sessions['a'*32]=session
        self.assertFalse(await relay.recover_tunnel(0))
        self.assertFalse(relay.health()['tunnelReachable'])
        self.clock[0]=6
        self.assertTrue(await relay.recover_tunnel(0))
        self.assertEqual(relay.core.restarts,1);self.assertEqual(relay.generation,1)
        media.close.assert_awaited_once();self.assertIsNone(session.media)
        self.assertTrue(await relay.recover_tunnel(0))
        self.assertEqual(relay.core.restarts,1,'a late failure from the old core cannot restart the new one')
        await relay.close()

    async def test_concurrent_failures_share_checks_and_restart_cooldown(self):
        relay=self.make_relay()
        await asyncio.gather(*(relay.recover_tunnel(0) for _ in range(8)))
        self.assertEqual(relay.check_tunnel.await_count,1)
        self.clock[0]=6
        await asyncio.gather(*(relay.recover_tunnel(0) for _ in range(8)))
        self.assertEqual(relay.core.restarts,1)
        for now in (12,18,24):
            self.clock[0]=now;await relay.recover_tunnel(1)
        self.assertEqual(relay.core.restarts,1,'a failed network must not cause a restart loop')
        self.clock[0]=37
        await relay.recover_tunnel(1)
        self.assertEqual(relay.core.restarts,2)
        await relay.close()

    async def test_healthy_existing_stream_is_not_interrupted(self):
        relay=self.make_relay();session=StreamSession('wss://test.discord.media/?v=9')
        session.connected=True;session.media=SimpleNamespace(first_down_at=0,close=AsyncMock());relay.sessions['a'*32]=session
        for now in (0,6,12):
            self.clock[0]=now;self.assertFalse(await relay.recover_tunnel(0))
        relay.check_tunnel.assert_not_awaited();self.assertEqual(relay.core.restarts,0)
        await relay.close()

    async def test_stream_connected_during_probe_prevents_restart(self):
        relay=self.make_relay();await relay.recover_tunnel(0);self.clock[0]=6
        session=StreamSession('wss://test.discord.media/?v=9');relay.sessions['a'*32]=session
        async def recovered_stream():
            session.connected=True;session.connected_at=self.clock[0];raise TimeoutError()
        relay.check_tunnel=AsyncMock(side_effect=recovered_stream)
        self.assertFalse(await relay.recover_tunnel(0))
        self.assertEqual(relay.core.restarts,0)
        await relay.close()

    async def test_reachable_tunnel_with_failed_remote_endpoint_is_not_restarted(self):
        relay=self.make_relay();relay.check_tunnel=AsyncMock()
        for now in (0,6,12):
            self.clock[0]=now;self.assertFalse(await relay.recover_tunnel(0))
        self.assertTrue(relay.health()['tunnelReachable']);self.assertEqual(relay.core.restarts,0)
        await relay.close()

    async def test_process_running_alone_does_not_mark_tunnel_reachable(self):
        relay=self.make_relay();health=relay.health()
        self.assertTrue(health['coreRunning']);self.assertIsNone(health['tunnelReachable'])
        self.assertEqual(health['ownerPID'],1234)
        await relay.close()

    async def test_proxy_failures_trigger_recovery_and_fresh_identification(self):
        relay=self.make_relay()
        @asynccontextmanager
        async def failing(target,**kwargs):
            raise ProxyError('synthetic proxy failure')
            yield
        relay.connector=failing
        try:
            async with serve(relay.handle,'127.0.0.1',0,compression=None) as server:
                port=server.sockets[0].getsockname()[1]
                for now,expected in ((0,1011),(6,4006)):
                    self.clock[0]=now
                    url=f'ws://127.0.0.1:{port}/relay?'+urlencode({'nonce':'synthetic','target':'wss://test.discord.media/?v=9','sid':'a'*32})
                    async with connect(url,proxy=None) as client:
                        with self.assertRaises(ConnectionClosed) as error:await asyncio.wait_for(client.recv(),2)
                        self.assertEqual(error.exception.rcvd.code,expected)
                    # Let the previous handler finish before reusing its session.
                    for _ in range(100):
                        if relay.stats['activeControls']==0:break
                        await asyncio.sleep(.001)
                self.assertEqual(relay.core.restarts,1)
        finally:await relay.close()

    async def test_status_file_lock_does_not_stop_media_service(self):
        relay=self.make_relay()
        with tempfile.TemporaryDirectory() as folder,patch('stream_relay.BASE',Path(folder)),patch.object(Path,'replace',side_effect=PermissionError),patch('stream_relay.asyncio.sleep',new=AsyncMock(side_effect=asyncio.CancelledError)):
            with self.assertRaises(asyncio.CancelledError):await relay.maintain()
        self.assertTrue(relay.core.running)
        self.assertEqual(relay.events[-1]['event'],'status-write-error')
        await relay.close()

    async def test_asyncio_timeout_also_checks_tunnel_on_python_310(self):
        relay=self.make_relay()
        @asynccontextmanager
        async def failing(target,**kwargs):
            raise asyncio.TimeoutError()
            yield
        relay.connector=failing
        try:
            async with serve(relay.handle,'127.0.0.1',0,compression=None) as server:
                port=server.sockets[0].getsockname()[1]
                url=f'ws://127.0.0.1:{port}/relay?'+urlencode({'nonce':'synthetic','target':'wss://test.discord.media/?v=9','sid':'a'*32})
                async with connect(url,proxy=None) as client:
                    with self.assertRaises(ConnectionClosed):await asyncio.wait_for(client.recv(),2)
                relay.check_tunnel.assert_awaited_once()
        finally:await relay.close()

    async def test_new_control_waits_until_core_restart_finishes(self):
        relay=self.make_relay();entered=asyncio.Event();release=asyncio.Event()
        connected=[]
        async def restart():entered.set();await release.wait()
        relay.core.restart=restart
        class Upstream:
            def __init__(self):self.frames=asyncio.Queue();self.frames.put_nowait('connected-after-restart')
            def __aiter__(self):return self
            async def __anext__(self):return await self.frames.get()
            async def send(self,frame):pass
        @asynccontextmanager
        async def connector(target,**kwargs):
            connected.append(True);yield Upstream()
        relay.connector=connector
        await relay.recover_tunnel(0);self.clock[0]=6
        recovering=asyncio.create_task(relay.recover_tunnel(0))
        try:
            await asyncio.wait_for(entered.wait(),2)
            async with serve(relay.handle,'127.0.0.1',0,compression=None) as server:
                port=server.sockets[0].getsockname()[1]
                url=f'ws://127.0.0.1:{port}/relay?'+urlencode({'nonce':'synthetic','target':'wss://test.discord.media/?v=9','sid':'a'*32})
                async with connect(url,proxy=None) as client:
                    async def pending():
                        while not relay.stats['activeControls']:await asyncio.sleep(.001)
                    await asyncio.wait_for(pending(),2)
                    self.assertFalse(connected,'a new stream cannot use the core being restarted')
                    release.set();await asyncio.wait_for(recovering,2)
                    self.assertEqual(await asyncio.wait_for(client.recv(),2),'connected-after-restart')
        finally:
            release.set();await asyncio.gather(recovering,return_exceptions=True);await relay.close()

    async def test_owner_shutdown_releases_core_before_waiting_for_server(self):
        core=Core()
        @asynccontextmanager
        async def server(*args,**kwargs):
            yield
            self.assertFalse(core.running,'the next Discord must not find the previous core still holding its port')
        owner=SimpleNamespace(alive=lambda:False,close=lambda:None)
        with patch('stream_relay.Core',return_value=core),patch('stream_relay.OwnerProcess',return_value=owner),patch('stream_relay.local_settings',return_value={'relayPort':19884}),patch('stream_relay.serve',server):
            await main(owner_pid=1234)
