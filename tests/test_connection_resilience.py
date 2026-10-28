import asyncio
from contextlib import asynccontextmanager
import json
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock,patch
from urllib.parse import urlencode

from websockets.asyncio.client import connect
from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosed,ProxyError

from stream_relay import Core,LocalDatagrams,MediaBridge,MediaStartupTimeout,Relay,StreamSession
from socks_udp import PacketQueue


TARGET='wss://test.discord.media/?v=9'
QUERY=b'\x00\x01\x00\x46'+b'\x00\x00\x00\x07'+bytes(66)
REPLY=b'\x00\x02'+QUERY[2:]


class FakeCore:
    running=True
    process=SimpleNamespace(pid=1)
    async def ensure(self):pass
    async def stop(self):self.running=False
    async def restart(self):pass


class Upstream:
    def __init__(self):self.frames=asyncio.Queue();self.frames.put_nowait('ready');self.received=[]
    def __aiter__(self):return self
    async def __anext__(self):return await self.frames.get()
    async def send(self,frame):self.received.append(frame)


class ResilienceTests(unittest.IsolatedAsyncioTestCase):
    def relay(self,connector=None):
        options={} if connector is None else {'connector':connector}
        return Relay({'nonce':'synthetic','socksPort':19885,'idleSeconds':40},core=FakeCore(),**options)

    def url(self,server):
        return f'ws://127.0.0.1:{server.sockets[0].getsockname()[1]}/relay?'+urlencode({'nonce':'synthetic','target':TARGET,'sid':'a'*32})

    async def test_transient_handshake_failure_is_retried_without_reconnecting_client(self):
        calls=[];upstream=Upstream()
        @asynccontextmanager
        async def connector(target,**options):
            calls.append(options)
            if len(calls)==1:raise ProxyError('synthetic')
            yield upstream
        relay=self.relay(connector)
        try:
            async with serve(relay.handle,'127.0.0.1',0) as server:
                async with connect(self.url(server),proxy=None) as client:
                    await client.send('identify-once')
                    self.assertEqual(await asyncio.wait_for(client.recv(),2),'ready')
                    for _ in range(100):
                        if upstream.received:break
                        await asyncio.sleep(.001)
                    self.assertEqual(upstream.received,['identify-once'])
            self.assertEqual(len(calls),2);self.assertEqual(relay.stats['controlRetries'],1)
            self.assertGreater(calls[0]['open_timeout'],14,'slow handshakes retain the original startup budget')
            self.assertLess(calls[1]['open_timeout'],calls[0]['open_timeout'])
        finally:await relay.close()

    async def test_exhausted_handshake_budget_is_not_restarted_from_zero(self):
        now=[0];calls=[]
        @asynccontextmanager
        async def connector(target,**options):
            calls.append(True);now[0]=15;raise TimeoutError();yield
        relay=self.relay(connector);relay.clock=lambda:now[0];relay.recover_tunnel=AsyncMock(return_value=False)
        try:
            async with serve(relay.handle,'127.0.0.1',0) as server:
                async with connect(self.url(server),proxy=None) as client:
                    with self.assertRaises(ConnectionClosed):await asyncio.wait_for(client.recv(),2)
            self.assertEqual(calls,[True]);self.assertEqual(relay.stats['controlRetries'],0)
        finally:await relay.close()

    async def test_terminal_handshake_failure_is_not_retried(self):
        calls=[]
        @asynccontextmanager
        async def connector(target,**options):
            calls.append(True);raise ValueError('terminal synthetic failure');yield
        relay=self.relay(connector)
        try:
            async with serve(relay.handle,'127.0.0.1',0) as server:
                async with connect(self.url(server),proxy=None) as client:
                    with self.assertRaises(ConnectionClosed):await asyncio.wait_for(client.recv(),2)
            self.assertEqual(calls,[True])
        finally:await relay.close()

    async def test_connected_control_does_not_erase_failed_udp_probes(self):
        @asynccontextmanager
        async def connector(target,**options):yield Upstream()
        relay=self.relay(connector);relay.failed_probes=1
        try:
            async with serve(relay.handle,'127.0.0.1',0) as server:
                async with connect(self.url(server),proxy=None) as client:
                    self.assertEqual(await asyncio.wait_for(client.recv(),2),'ready')
                    self.assertEqual(relay.failed_probes,1)
        finally:await relay.close()

    async def test_leaving_stream_cancels_pending_upstream_without_retry(self):
        entered=asyncio.Event();cancelled=asyncio.Event();calls=[]
        @asynccontextmanager
        async def connector(target,**options):
            calls.append(True);entered.set()
            try:await asyncio.Future()
            finally:cancelled.set()
            yield
        relay=self.relay(connector)
        try:
            async with serve(relay.handle,'127.0.0.1',0) as server:
                async with connect(self.url(server),proxy=None):await asyncio.wait_for(entered.wait(),2)
                await asyncio.wait_for(cancelled.wait(),2)
            self.assertEqual(calls,[True]);self.assertEqual(relay.stats['activeControls'],0)
        finally:await relay.close()

    async def test_slow_background_warmup_does_not_block_new_stream(self):
        entered=asyncio.Event();release=asyncio.Event()
        @asynccontextmanager
        async def connector(target,**options):yield Upstream()
        relay=self.relay(connector)
        async def check():entered.set();await release.wait()
        relay.check_tunnel=check
        warmer=asyncio.create_task(relay.warm())
        try:
            await asyncio.wait_for(entered.wait(),2)
            async with serve(relay.handle,'127.0.0.1',0) as server:
                async with connect(self.url(server),proxy=None) as client:
                    self.assertEqual(await asyncio.wait_for(client.recv(),1),'ready')
                    self.assertFalse(warmer.done())
        finally:
            release.set();await warmer;await relay.close()

    async def test_old_probe_cannot_overwrite_new_core_health(self):
        relay=self.relay();entered=asyncio.Event();release=asyncio.Event()
        async def check():entered.set();await release.wait();raise TimeoutError()
        relay.check_tunnel=check
        probe=asyncio.create_task(relay.verify_tunnel())
        await entered.wait();relay.generation+=1;relay.tunnel_reachable=True
        release.set()
        with self.assertRaises(TimeoutError):await probe
        self.assertTrue(relay.tunnel_reachable);await relay.close()

    async def test_owned_discord_keeps_core_warm_after_idle_window(self):
        relay=self.relay();relay.owner_pid=1234;relay.last_control=time.monotonic()-1000
        relay.core.stop=AsyncMock()
        with tempfile.TemporaryDirectory() as directory,patch('stream_relay.BASE',Path(directory)),patch('stream_relay.asyncio.sleep',new=AsyncMock(side_effect=asyncio.CancelledError)):
            with self.assertRaises(asyncio.CancelledError):await relay.maintain()
        relay.core.stop.assert_not_awaited()
        relay.owner_pid=None
        with tempfile.TemporaryDirectory() as directory,patch('stream_relay.BASE',Path(directory)),patch('stream_relay.asyncio.sleep',new=AsyncMock(side_effect=asyncio.CancelledError)):
            with self.assertRaises(asyncio.CancelledError):await relay.maintain()
        relay.core.stop.assert_awaited_once();await relay.close()

    async def test_failed_background_warmup_retries_with_bounded_backoff(self):
        relay=self.relay();relay.warm=AsyncMock(side_effect=TimeoutError);relay.recover_tunnel=AsyncMock()
        waits=[]
        async def sleep(delay):
            waits.append(delay)
            if len(waits)==6:relay.stopping=True
        with patch('stream_relay.asyncio.sleep',side_effect=sleep):await relay.preheat()
        self.assertEqual(waits,[5,10,20,40,60,60]);self.assertEqual(relay.recover_tunnel.await_count,6)
        await relay.close()

    async def test_connected_control_without_udp_eventually_allows_recovery(self):
        relay=self.relay();now=[0];relay.clock=lambda:now[0]
        session=StreamSession(TARGET);session.connected=True;session.connected_at=0;relay.sessions['a'*32]=session
        self.assertTrue(relay.healthy_streams())
        now[0]=16;self.assertFalse(relay.healthy_streams())
        session.media=SimpleNamespace(first_down_at=0,close=AsyncMock())
        self.assertTrue(relay.healthy_streams(),'a paused but established stream stays protected')
        await relay.close()

    async def test_temporary_socks_congestion_does_not_replace_running_core(self):
        core=Core({'socksPort':19885});core.process=SimpleNamespace(poll=lambda:None)
        core.probe=AsyncMock(side_effect=TimeoutError);core._stop=AsyncMock()
        with self.assertRaisesRegex(ConnectionError,'CoreProxyUnavailable'):await core.ensure()
        core._stop.assert_not_awaited()

    async def test_reclaim_inactive_sessions_when_switching_streams_quickly(self):
        relay=self.relay()
        for index in range(8):
            session=StreamSession(TARGET);session.closed_at=index;session.media=SimpleNamespace(close=AsyncMock())
            relay.sessions[f'{index:032x}']=session
        oldest=relay.sessions['0'*32].media
        request=SimpleNamespace(headers={'Origin':'https://discord.com'},path='/relay?'+urlencode({'nonce':'synthetic','target':TARGET,'sid':'f'*32}))
        response=await relay.validate(SimpleNamespace(respond=lambda *args:args),request)
        self.assertIsNone(response);self.assertEqual(len(relay.sessions),7);oldest.close.assert_awaited_once()
        await relay.close()


class DiscoveryTests(unittest.IsolatedAsyncioTestCase):
    def bridge(self):
        queue=asyncio.Queue()
        async def receive():return await queue.get()
        socks=SimpleNamespace(send=AsyncMock(),receive=receive,close=AsyncMock())
        stats={'udpQueueDrops':0,'udpUpBytes':0,'udpUpPackets':0,'udpDownBytes':0,'udpDownPackets':0}
        bridge=MediaBridge(socks,('8.8.8.8',50000),stats)
        bridge.transport=SimpleNamespace(sendto=lambda *args:None,close=lambda:None)
        return bridge,queue

    async def test_lost_discovery_packet_is_retried_and_matching_reply_stops_retries(self):
        bridge,queue=self.bridge();protocol=LocalDatagrams(bridge)
        protocol.datagram_received(QUERY,('127.0.0.1',12345))
        retry=asyncio.Event()
        async def send(target,data):
            if bridge.stats.get('udpUpPackets',0):
                self.assertEqual(data,QUERY);await queue.put((bridge.target,REPLY));retry.set()
        bridge.socks.send.side_effect=send
        bridge.tasks=[asyncio.create_task(bridge.outbound()),asyncio.create_task(bridge.inbound()),asyncio.create_task(bridge.discover())]
        try:
            await asyncio.wait_for(retry.wait(),1)
            await asyncio.wait_for(bridge.discovery_done.wait(),1)
            await asyncio.sleep(.01)
            self.assertEqual(bridge.stats['udpDiscoveryRetries'],1)
            self.assertIsNotNone(bridge.discovery_ms)
            self.assertTrue(all(not task.done() for task in bridge.tasks))
        finally:await bridge.close()

    async def test_no_udp_reply_times_out_and_requests_fresh_session_once(self):
        bridge,_=self.bridge();bridge.startup_timeout=.03
        LocalDatagrams(bridge).datagram_received(QUERY,('127.0.0.1',12345))
        discovery=asyncio.create_task(bridge.discover());bridge.tasks=[discovery]
        with self.assertRaises(MediaStartupTimeout):await discovery
        relay=Relay({'idleSeconds':40},core=FakeCore())
        relay.recover_tunnel=AsyncMock(return_value=False)
        session=StreamSession(TARGET);session.active=True;session.media=bridge;session.client=SimpleNamespace(close=AsyncMock())
        relay.sessions['a'*32]=session
        async def sleep(_):raise asyncio.CancelledError()
        with tempfile.TemporaryDirectory() as directory,patch('stream_relay.BASE',Path(directory)),patch('stream_relay.asyncio.sleep',side_effect=sleep):
            for _ in range(2):
                with self.assertRaises(asyncio.CancelledError):await relay.maintain()
        session.client.close.assert_awaited_once_with(4006)
        await asyncio.sleep(0)
        relay.recover_tunnel.assert_awaited_once_with(0)
        self.assertEqual(relay.stats['mediaStartupTimeouts'],1);self.assertIsNone(session.media)
        await relay.close()

    async def test_encrypted_media_and_unmatched_discovery_are_never_replayed(self):
        bridge,queue=self.bridge();protocol=LocalDatagrams(bridge)
        protocol.datagram_received(b'\x80\x60encrypted-media',('127.0.0.1',12345))
        self.assertIsNone(bridge.discovery_packet)
        protocol.datagram_received(QUERY,('127.0.0.1',12345))
        inbound=asyncio.create_task(bridge.inbound());bridge.tasks=[inbound]
        try:
            await queue.put((bridge.target,REPLY[:7]+b'\xff'+REPLY[8:]))
            for _ in range(100):
                if bridge.first_down_at is not None:break
                await asyncio.sleep(.001)
            self.assertFalse(bridge.discovery_done.is_set())
            self.assertEqual(bridge.socks.send.await_count,0)
        finally:await bridge.close()


if __name__=='__main__':unittest.main()
