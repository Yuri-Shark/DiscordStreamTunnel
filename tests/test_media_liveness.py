"""Exercise established UDP paths that silently stop delivering packets."""
import asyncio
from pathlib import Path
import struct
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock,patch

from stream_relay import LocalDatagrams,MediaBridge,Relay,StreamSession


QUERY=b'\x00\x01\x00\x46'+struct.pack('!I',7)+bytes(66)
def reply(address='203.0.113.1',port=50000):
    return b'\x00\x02\x00\x46'+QUERY[4:8]+address.encode().ljust(64,b'\0')+struct.pack('!H',port)


class MediaLivenessTests(unittest.IsolatedAsyncioTestCase):
    async def make_bridge(self,answer):
        queue=asyncio.Queue();delivered=[];sent=[]
        async def receive():return await queue.get()
        async def send(target,data):
            sent.append(data)
            result=reply() if len(sent)==1 else answer(len(sent)-1)
            if result is not None:await queue.put((target,result))
        socks=SimpleNamespace(send=send,receive=receive,close=AsyncMock())
        stats=dict(udpQueueDrops=0,udpUpBytes=0,udpDownBytes=0,udpUpPackets=0,udpDownPackets=0)
        bridge=MediaBridge(socks,('8.8.8.8',50000),stats)
        bridge.path_check_interval=.03;bridge.path_check_timeout=.06
        bridge.transport=SimpleNamespace(sendto=lambda data,peer:delivered.append(data),close=lambda:None)
        LocalDatagrams(bridge).datagram_received(QUERY,('127.0.0.1',12345))
        bridge.tasks=[asyncio.create_task(bridge.outbound()),asyncio.create_task(bridge.inbound()),asyncio.create_task(bridge.discover())]
        self.addAsyncCleanup(bridge.close)
        await asyncio.wait_for(bridge.discovery_done.wait(),1)
        return bridge,queue,delivered,sent

    async def until(self,predicate):
        async def wait():
            while not predicate():await asyncio.sleep(.001)
        await asyncio.wait_for(wait(),5)

    async def test_paused_stream_answers_checks_without_reconnect_or_synthetic_replies_to_discord(self):
        bridge,_,delivered,sent=await self.make_bridge(lambda _:reply())
        await self.until(lambda:bridge.stats.get('udpPathChecks',0)>=3)
        await asyncio.sleep(.005)
        self.assertTrue(all(not task.done() for task in bridge.tasks),'paused but reachable video must stay connected')
        self.assertGreaterEqual(len(sent),4)
        self.assertEqual(delivered,[reply()],'only the native discovery response belongs to Discord')
        self.assertTrue(all(packet==QUERY for packet in sent),'only idempotent discovery may be retried')

    async def test_packet_loss_retries_the_probe_without_replaying_media(self):
        bridge,_,delivered,sent=await self.make_bridge(lambda attempt:reply() if attempt%3==0 else None)
        await self.until(lambda:len(sent)>=4)
        await asyncio.sleep(.005)
        self.assertTrue(all(not task.done() for task in bridge.tasks))
        self.assertEqual(bridge.stats['udpPathProbes'],3)
        self.assertEqual(delivered,[reply()])

    async def test_resumed_media_satisfies_check_even_when_discovery_reply_is_lost(self):
        packet=b'\x80\x60encrypted-placeholder'
        bridge,_,delivered,_=await self.make_bridge(lambda _:packet)
        await self.until(lambda:bridge.stats.get('udpPathChecks',0)>=2)
        await asyncio.sleep(.005)
        self.assertTrue(all(not task.done() for task in bridge.tasks))
        self.assertIn(packet,delivered)

    async def test_active_media_does_not_generate_discovery_probes(self):
        # A loaded Windows runner can suspend the test for longer than 30 ms.
        # Advance logical media time explicitly instead of testing its scheduler.
        clock=[0]
        with patch('stream_relay.time',SimpleNamespace(monotonic=lambda:clock[0],time=time.time)):
            bridge,queue,_,sent=await self.make_bridge(lambda _:None)
            for _ in range(20):
                clock[0]+=.005
                await queue.put((bridge.target,b'\x80\x60encrypted-placeholder'))
                await asyncio.sleep(.005)
            self.assertEqual(sent,[QUERY])
            self.assertTrue(all(not task.done() for task in bridge.tasks))
            await bridge.close()

    async def test_dead_established_path_is_closed_once_and_schedules_recovery(self):
        bridge,_,_,sent=await self.make_bridge(lambda _:None)
        await self.until(lambda:any(task.done() for task in bridge.tasks))
        errors=[task.exception() for task in bridge.tasks if task.done()]
        self.assertEqual([type(error).__name__ for error in errors],['MediaPathTimeout'])
        self.assertEqual(len(sent),4,'initial discovery plus three bounded path probes')
        core=SimpleNamespace(running=True,process=SimpleNamespace(pid=1),stop=AsyncMock())
        relay=Relay({'idleSeconds':40},core=core);self.addAsyncCleanup(relay.close)
        relay.recover_tunnel=AsyncMock(return_value=False)
        session=StreamSession('wss://test.discord.media/?v=9')
        session.active=session.connected=True;session.connected_at=time.monotonic()-120
        session.media=bridge;session.client=SimpleNamespace(close=AsyncMock());relay.sessions['a'*32]=session
        self.assertFalse(relay.healthy_streams(),'historical UDP must not protect a failed transport forever')
        with tempfile.TemporaryDirectory() as directory,patch('stream_relay.BASE',Path(directory)),patch('stream_relay.asyncio.sleep',new=AsyncMock(side_effect=asyncio.CancelledError)):
            for _ in range(2):
                with self.assertRaises(asyncio.CancelledError):await relay.maintain()
        await asyncio.sleep(0)
        session.client.close.assert_awaited_once_with(4006)
        relay.recover_tunnel.assert_awaited_once_with(0)
        self.assertEqual(relay.stats['mediaPathTimeouts'],1)
        self.assertIsNone(session.media)

    async def test_changed_external_mapping_requests_fresh_negotiation(self):
        changed=reply(port=50001)
        bridge,_,delivered,_=await self.make_bridge(lambda _:changed)
        await self.until(lambda:any(task.done() for task in bridge.tasks))
        errors=[task.exception() for task in bridge.tasks if task.done()]
        self.assertEqual([type(error).__name__ for error in errors],['MediaEndpointChanged'])
        self.assertEqual(delivered,[reply()],'a changed address cannot be silently injected into negotiated media')

    async def test_bridge_close_cancels_pending_checks_and_sends_no_later_packets(self):
        bridge,_,_,sent=await self.make_bridge(lambda _:None)
        await self.until(lambda:len(sent)>1)
        await bridge.close();count=len(sent)
        await asyncio.sleep(.04)
        self.assertEqual(len(sent),count)
        self.assertTrue(all(task.cancelled() for task in bridge.tasks))

    async def test_long_lived_path_recovers_from_later_loss_instead_of_trusting_initial_success(self):
        bridge,_,_,_=await self.make_bridge(lambda attempt:reply() if attempt<=120 else None)
        # Accelerate 120 quiet checks (one hour at the production 30s cadence),
        # then blackhole the association without closing its SOCKS control TCP.
        # Probe response deadlines must exceed the Windows clock resolution;
        # otherwise a healthy reply can be counted as loss on a busy runner.
        bridge.path_check_interval=.002;bridge.path_check_timeout=.3
        await self.until(lambda:any(task.done() for task in bridge.tasks))
        errors=[task.exception() for task in bridge.tasks if task.done()]
        self.assertEqual([type(error).__name__ for error in errors],['MediaPathTimeout'])
        self.assertEqual(bridge.stats['udpPathChecks'],121)

    async def test_unmatched_discovery_and_other_sources_cannot_keep_dead_path_alive(self):
        wrong_ssrc=reply()[:4]+struct.pack('!I',8)+reply()[8:]
        bridge,queue,_,_=await self.make_bridge(lambda _:wrong_ssrc)
        for _ in range(25):
            await queue.put((('8.8.4.4',50000),reply()))
            await asyncio.sleep(.005)
        await self.until(lambda:any(task.done() for task in bridge.tasks))
        errors=[task.exception() for task in bridge.tasks if task.done()]
        self.assertEqual([type(error).__name__ for error in errors],['MediaPathTimeout'])


if __name__=='__main__':unittest.main()
