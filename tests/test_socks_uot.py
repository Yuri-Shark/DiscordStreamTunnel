"""Real loopback TCP framing, isolation, cancellation and failure handling."""
import asyncio
from contextlib import asynccontextmanager
from importlib.util import find_spec
import ipaddress
import struct
import unittest
import json
from pathlib import Path
import tempfile

from configuration import local_settings
from diagnostics import runtime_snapshot
from stream_relay import MediaBridge

from socks_uot import SocksUOT


@asynccontextmanager
async def proxy(reject=None, fragment=False, username=b'media'):
    writers, tasks, targets, packets = set(), set(), [], []

    async def handle(reader, writer):
        writers.add(writer)
        tasks.add(asyncio.current_task())
        try:
            assert await reader.readexactly(3) == b'\x05\x01\x02'
            writer.write(b'\x05\x00' if reject == 'method' else b'\x05\x02')
            await writer.drain()
            if reject == 'method':
                return
            assert await reader.readexactly(1) == b'\x01'
            user = await reader.readexactly((await reader.readexactly(1))[0])
            secret = await reader.readexactly((await reader.readexactly(1))[0])
            assert (user, secret) == (username, b'synthetic')
            writer.write(b'\x01\x01' if reject == 'auth' else b'\x01\x00')
            await writer.drain()
            if reject == 'auth':
                return
            assert await reader.readexactly(4) == b'\x05\x01\x00\x03'
            assert await reader.readexactly((await reader.readexactly(1))[0]) == b'sp.v2.udp-over-tcp.arpa'
            assert await reader.readexactly(2) == b'\0\0'
            writer.write(b'\x05\x01\x00\x01' + bytes(6) if reject == 'connect'
                         else b'\x05\x00\x00\x01' + bytes(6))
            await writer.drain()
            if reject == 'connect':
                return
            assert await reader.readexactly(1) == b'\x01'
            atyp = (await reader.readexactly(1))[0]
            address = str(ipaddress.ip_address(await reader.readexactly({1: 4, 4: 16}[atyp])))
            port = struct.unpack('!H', await reader.readexactly(2))[0]
            targets.append((address, port))
            while True:
                length = struct.unpack('!H', await reader.readexactly(2))[0]
                packet = await reader.readexactly(length)
                packets.append(packet)
                if reject == 'frame-size':
                    writer.write(b'\xff\xff')
                else:
                    frame = struct.pack('!H', length) + packet
                    if fragment:
                        writer.write(frame[:1])
                        await writer.drain()
                        await asyncio.sleep(0)
                        writer.write(frame[1:7])
                        await writer.drain()
                        await asyncio.sleep(0)
                        writer.write(frame[7:])
                    else:
                        writer.write(frame)
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                pass
            writers.discard(writer)
            tasks.discard(asyncio.current_task())

    server = await asyncio.start_server(handle, '127.0.0.1', 0)
    try:
        yield server.sockets[0].getsockname()[1], writers, targets, packets
    finally:
        server.close()
        await server.wait_closed()
        for writer in list(writers):
            writer.close()
        if tasks:
            await asyncio.wait_for(asyncio.gather(*list(tasks)), 2)


class SocksUOTTests(unittest.IsolatedAsyncioTestCase):
    async def test_native_udp_bridge_uses_local_tcp_and_preserves_payloads(self):
        async with proxy(username=b'discord-stream') as (port, _, targets, _):
            stats = dict(udpUpBytes=0, udpUpPackets=0, udpDownBytes=0, udpDownPackets=0, udpQueueDrops=0)
            bridge = await MediaBridge.create({'socksPort': port, 'nonce': 'synthetic',
                'mediaTransport': 'uot2'}, ('8.8.8.8', 50000), stats)
            replies = asyncio.Queue()
            class Native(asyncio.DatagramProtocol):
                def datagram_received(self, data, address): replies.put_nowait(data)
            transport, _ = await asyncio.get_running_loop().create_datagram_endpoint(
                Native, local_addr=('127.0.0.1', 0))
            try:
                self.assertEqual(bridge.socks.kind, 'uot2')
                payloads = [b'native' + bytes([i]) for i in range(128)]
                for payload in payloads:
                    transport.sendto(payload, ('127.0.0.1', bridge.local_port))
                received = [await asyncio.wait_for(replies.get(), 2) for _ in payloads]
                self.assertEqual(received, payloads)
                self.assertEqual(targets, [('8.8.8.8', 50000)])
                self.assertEqual(stats['udpUpPackets'], 128)
                self.assertEqual(stats['udpDownPackets'], 128)
            finally:
                transport.close()
                await bridge.close()

    @unittest.skipUnless(find_spec('selective_relay') is not None, 'Optional standalone edition is not present')
    async def test_selective_edition_uses_same_transport_without_driver_or_direct_fallback(self):
        from selective_relay import Relay
        async with proxy() as (port, _, _, _):
            relay = Relay({'port': port, 'username': 'media', 'password': 'synthetic', 'transport': 'uot2'})
            association = await relay.create_association()
            try:
                await association.send(('8.8.8.8', 50000), b'screen')
                self.assertEqual(await asyncio.wait_for(association.receive(), 1), (('8.8.8.8', 50000), b'screen'))
                self.assertEqual(relay.snapshot()['localMediaTransport'], 'uot2')
            finally:
                await association.close()

    async def test_ipv4_ipv6_fragmented_frames_preserve_udp_boundaries_bytes_and_order(self):
        async with proxy(fragment=True) as (port, _, targets, packets):
            for target in [('8.8.8.8', 50000), ('2001:4860:4860::8888', 50001)]:
                association = await SocksUOT.create(port, 'media', 'synthetic')
                payloads = [b'', b'a', bytes(range(256)) * 4, bytes(65507)]
                receiving = asyncio.create_task(self.collect(association, len(payloads)))
                try:
                    for payload in payloads:
                        await association.send(target, payload)
                    self.assertEqual(await asyncio.wait_for(receiving, 2), [(target, p) for p in payloads])
                finally:
                    receiving.cancel()
                    await asyncio.gather(receiving, return_exceptions=True)
                    await association.close()
            self.assertEqual(targets, [('8.8.8.8', 50000), ('2001:4860:4860::8888', 50001)])
            self.assertEqual(packets, payloads * 2)

    async def collect(self, association, count):
        return [await association.receive() for _ in range(count)]

    async def test_pressure_on_one_screen_does_not_block_another_and_cancelled_send_is_not_delivered(self):
        async with proxy() as (port, _, _, packets):
            slow = await SocksUOT.create(port, 'media', 'synthetic')
            healthy = await SocksUOT.create(port, 'media', 'synthetic')
            slow.protocol.stats = {}
            slow.protocol.pause_writing()
            pending = asyncio.create_task(slow.send(('8.8.8.8', 50000), b'cancelled'))
            try:
                await asyncio.sleep(0)
                self.assertFalse(pending.done())
                await healthy.send(('8.8.8.8', 50001), b'healthy')
                self.assertEqual(await asyncio.wait_for(healthy.receive(), 1), (('8.8.8.8', 50001), b'healthy'))
                pending.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await pending
                slow.protocol.resume_writing()
                await slow.send(('8.8.8.8', 50000), b'resumed')
                self.assertEqual(await asyncio.wait_for(slow.receive(), 1), (('8.8.8.8', 50000), b'resumed'))
                self.assertNotIn(b'cancelled', packets)
                self.assertEqual(slow.protocol.stats['localMediaSendBackpressure'], 1)
            finally:
                pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)
                await slow.close()
                await healthy.close()

    async def test_permanent_pressure_times_out_without_queuing_media(self):
        async with proxy() as (port, _, _, packets):
            association = await SocksUOT.create(port, 'media', 'synthetic')
            association.protocol.stats = {}
            association.protocol.write_timeout = .02
            association.protocol.pause_writing()
            try:
                with self.assertRaisesRegex(TimeoutError, 'LocalMediaWriteTimeout'):
                    await asyncio.wait_for(association.send(('8.8.8.8', 50000), b'blocked'), 1)
                self.assertEqual(association.protocol.stats['localMediaSendTimeouts'], 1)
                self.assertEqual(packets, [])
            finally:
                association.protocol.resume_writing()
                await association.close()

    async def test_remote_close_before_first_packet_wakes_receive_and_control(self):
        async with proxy() as (port, writers, _, _):
            association = await SocksUOT.create(port, 'media', 'synthetic')
            receiving = asyncio.create_task(association.receive())
            watching = asyncio.create_task(association.watch_control())
            try:
                for writer in list(writers):
                    writer.close()
                results = await asyncio.wait_for(asyncio.gather(receiving, watching, return_exceptions=True), 1)
                self.assertTrue(all(isinstance(error, ConnectionError) for error in results))
                with self.assertRaises(ConnectionError):
                    await association.send(('8.8.8.8', 50000), b'late')
            finally:
                await association.close()

    async def test_authentication_and_connect_rejections_close_without_fallback(self):
        for rejection in ['method', 'auth', 'connect']:
            async with proxy(reject=rejection) as (port, _, targets, packets):
                with self.assertRaises(ConnectionError):
                    await SocksUOT.create(port, 'media', 'synthetic')
                self.assertEqual(targets, [])
                self.assertEqual(packets, [])

    async def test_oversized_incoming_frame_fails_the_association_without_resynchronizing_payload(self):
        async with proxy(reject='frame-size') as (port, _, _, _):
            association = await SocksUOT.create(port, 'media', 'synthetic')
            try:
                await association.send(('8.8.8.8', 50000), b'packet')
                with self.assertRaisesRegex(ConnectionError, 'Invalid media frame size'):
                    await asyncio.wait_for(association.receive(), 1)
                with self.assertRaises(ConnectionError):
                    await asyncio.wait_for(association.watch_control(), 1)
            finally:
                await association.close()

    async def test_destination_change_and_oversized_send_do_not_corrupt_the_next_frame(self):
        async with proxy() as (port, _, _, packets):
            association = await SocksUOT.create(port, 'media', 'synthetic')
            target = ('8.8.8.8', 50000)
            try:
                await association.send(target, b'first')
                with self.assertRaises(ValueError):
                    await association.send(('8.8.4.4', 50000), b'wrong-destination')
                with self.assertRaises(ValueError):
                    await association.send(target, bytes(65508))
                await association.send(target, b'second')
                self.assertEqual(await asyncio.wait_for(self.collect(association, 2), 1), [(target, b'first'), (target, b'second')])
                self.assertEqual(packets, [b'first', b'second'])
            finally:
                await association.close()


class LocalTransportSettingsTests(unittest.TestCase):
    def test_existing_settings_gain_transport_without_overwriting_secrets_ports_or_explicit_choice(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            self.assertEqual(local_settings(base)['mediaTransport'], 'uot2')
            self.assertIs(local_settings(base)['tunnelGateway'], False)
            path = base / 'local-settings.json'
            original = {'nonce': 'synthetic', 'socksPort': 50101, 'relayPort': 50102}
            path.write_text(json.dumps(original), encoding='utf-8')
            self.assertEqual(local_settings(base), dict(original, mediaTransport='uot2', tunnelGateway=False))
            self.assertEqual(json.loads(path.read_text()), original)
            original['mediaTransport'] = 'udp'
            path.write_text(json.dumps(original), encoding='utf-8')
            self.assertEqual(local_settings(base), dict(original, tunnelGateway=False))
            original['tunnelGateway'] = True
            path.write_text(json.dumps(original), encoding='utf-8')
            self.assertEqual(local_settings(base), original)

    def test_transport_diagnostics_allow_known_labels_and_counters_only(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            path = base / 'status.json'
            path.write_text(json.dumps({'localMediaTransport': 'uot2', 'localMediaSendBackpressure': 2,
                'localMediaSendTimeouts': 1, 'streams': [{'localTransport': 'uot2'},
                {'localTransport': 'DO-NOT-SHARE'}]}), encoding='utf-8')
            result = runtime_snapshot(base)
            self.assertEqual(result['localMediaTransport'], 'uot2')
            self.assertEqual(result['localMediaSendTimeouts'], 1)
            self.assertEqual(result['localMediaSendBackpressure'], 2)
            self.assertEqual(result['streams'][0]['localTransport'], 'uot2')
            self.assertNotIn('DO-NOT-SHARE', json.dumps(result))


if __name__ == '__main__':
    unittest.main()
