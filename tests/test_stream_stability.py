"""Congested local UDP writes and slow diagnostics must not freeze other media."""
import asyncio
import os
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock,patch

from socks_udp import PacketQueue,SocksUDP,create_loopback_endpoint,unpack_packet
from stream_relay import MediaBridge,Relay


class StreamStabilityTests(unittest.IsolatedAsyncioTestCase):
    async def test_socks_send_waits_for_transport_capacity_and_preserves_order(self):
        protocol=PacketQueue();sent=[];advanced=asyncio.Event()
        def sendto(packet,relay):
            sent.append(unpack_packet(packet)[1]);advanced.set();protocol.pause_writing()
        socks=SocksUDP(None,None,SimpleNamespace(sendto=sendto),protocol,('127.0.0.1',19885))
        async def burst():
            for payload in (b'first',b'second',b'third'):await socks.send(('8.8.8.8',50000),payload)
        task=asyncio.create_task(burst())
        try:
            await asyncio.sleep(0)
            self.assertEqual(sent,[b'first'],'sendto must not fill an unbounded transport queue')
            advanced.clear();protocol.resume_writing();await asyncio.wait_for(advanced.wait(),1)
            self.assertEqual(sent,[b'first',b'second'])
            protocol.resume_writing();await asyncio.wait_for(task,1)
            self.assertEqual(sent,[b'first',b'second',b'third'])
        finally:task.cancel();await asyncio.gather(task,return_exceptions=True)

    async def test_slow_receiver_does_not_block_another_stream(self):
        async def bridge():
            incoming=asyncio.Queue();sent=[]
            media=MediaBridge(SimpleNamespace(receive=incoming.get),('8.8.8.8',50000),
                dict(udpDownBytes=0,udpDownPackets=0))
            media.peer=('127.0.0.1',12345)
            media.delivered=asyncio.Event()
            def sendto(data,peer):
                sent.append(data)
                if len(sent)==2:media.delivered.set()
            media.transport=SimpleNamespace(sendto=sendto)
            # SOCKS receive returns (target, payload).
            await incoming.put((media.target,b'encrypted-1'))
            await incoming.put((media.target,b'encrypted-2'))
            return media,incoming,sent
        slow,_,slow_sent=await bridge();healthy,_,healthy_sent=await bridge()
        slow.local_protocol.pause_writing()
        tasks=[asyncio.create_task(media.inbound()) for media in (slow,healthy)]
        try:
            await asyncio.sleep(0)
            self.assertEqual(slow_sent,[],'congested Discord socket must apply backpressure')
            self.assertEqual(healthy_sent,[b'encrypted-1',b'encrypted-2'])
            slow.local_protocol.resume_writing();await asyncio.wait_for(slow.delivered.wait(),1)
            self.assertEqual(slow_sent,healthy_sent,'encrypted datagrams must retain bytes and order')
            self.assertEqual(slow.stats['udpSendBackpressure'],1)
        finally:
            for task in tasks:task.cancel()
            await asyncio.gather(*tasks,return_exceptions=True)

    async def test_close_wakes_a_blocked_sender_without_sending_to_a_closed_socket(self):
        protocol=PacketQueue();sent=[];protocol.pause_writing()
        socks=SocksUDP(None,None,SimpleNamespace(sendto=lambda *args:sent.append(args)),protocol,('127.0.0.1',19885))
        task=asyncio.create_task(socks.send(('8.8.8.8',50000),b'encrypted'))
        try:
            await asyncio.sleep(0);self.assertFalse(task.done())
            protocol.connection_lost(None)
            with self.assertRaises(ConnectionError):await asyncio.wait_for(task,1)
            self.assertEqual(sent,[])
        finally:task.cancel();await asyncio.gather(task,return_exceptions=True)

    async def test_cancelling_a_congested_bridge_does_not_hang(self):
        protocol=PacketQueue();protocol.pause_writing()
        socks=SocksUDP(None,None,SimpleNamespace(sendto=lambda *args:None),protocol,('127.0.0.1',19885))
        task=asyncio.create_task(socks.send(('8.8.8.8',50000),b'encrypted'))
        await asyncio.sleep(0);self.assertFalse(task.done())
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):await asyncio.wait_for(task,1)
        protocol.resume_writing()
        await asyncio.wait_for(socks.send(('8.8.8.8',50000),b'new-session'),1)

    async def test_permanently_congested_socket_times_out_instead_of_waiting_forever(self):
        protocol=PacketQueue();protocol.write_timeout=.02;protocol.stats={};protocol.pause_writing()
        sent=[]
        socks=SocksUDP(None,None,SimpleNamespace(sendto=lambda *args:sent.append(args)),protocol,('127.0.0.1',19885))
        with self.assertRaisesRegex(TimeoutError,'LocalUDPWriteTimeout'):
            await asyncio.wait_for(socks.send(('8.8.8.8',50000),b'encrypted'),1)
        self.assertEqual(sent,[]);self.assertEqual(protocol.stats['udpSendTimeouts'],1)

    async def test_real_udp_burst_honors_windows_watermarks_without_loss(self):
        receiver,incoming=await create_loopback_endpoint(PacketQueue)
        sender,outgoing=await create_loopback_endpoint(PacketQueue)
        outgoing.stats={}
        socks=SocksUDP(None,None,sender,outgoing,receiver.get_extra_info('sockname'))
        payloads=[index.to_bytes(4,'big')+bytes(1196) for index in range(400)]
        async def receive():
            return [unpack_packet((await incoming.queue.get())[0])[1] for _ in payloads]
        receiving=asyncio.create_task(receive())
        try:
            for payload in payloads:await socks.send(('8.8.8.8',50000),payload)
            self.assertEqual(await asyncio.wait_for(receiving,5),payloads)
            self.assertEqual(incoming.drops,0)
            if os.name=='nt':self.assertGreater(outgoing.stats.get('udpSendBackpressure',0),0)
        finally:
            receiving.cancel();await asyncio.gather(receiving,return_exceptions=True)
            sender.close();receiver.close()

    async def test_real_udp_keeps_flowing_while_status_disk_write_is_blocked(self):
        entered=threading.Event();release=threading.Event();finished=threading.Event()
        original=Path.write_text;writes=[]
        def slow_write(file,*args,**kwargs):
            if file.name=='status.tmp':
                writes.append(threading.get_ident());entered.set()
                release.wait(.5)
                try:return original(file,*args,**kwargs)
                finally:finished.set()
            return original(file,*args,**kwargs)
        relay=Relay({'idleSeconds':40},core=SimpleNamespace(running=False,stop=AsyncMock()))
        receiver,protocol=await create_loopback_endpoint(PacketQueue)
        sender,_=await create_loopback_endpoint(PacketQueue)
        with tempfile.TemporaryDirectory() as directory,patch('stream_relay.BASE',Path(directory)),patch.object(Path,'write_text',slow_write):
            maintenance=asyncio.create_task(relay.maintain())
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait,1))
                sender.sendto(b'media-during-slow-disk',receiver.get_extra_info('sockname'))
                packet,_=await asyncio.wait_for(protocol.queue.get(),.2)
                self.assertEqual(packet,b'media-during-slow-disk')
                self.assertFalse(finished.is_set(),'diagnostic I/O must not occupy the UDP event loop')
                self.assertNotEqual(writes[0],threading.get_ident())
            finally:
                release.set();await asyncio.to_thread(finished.wait,1)
                maintenance.cancel();await asyncio.gather(maintenance,return_exceptions=True)
                receiver.close();sender.close();await relay.close()


if __name__=='__main__':unittest.main()
