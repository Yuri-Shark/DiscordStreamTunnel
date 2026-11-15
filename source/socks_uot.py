"""One media destination over authenticated loopback SOCKS / SagerNet UoT v2.

TCP carries framed datagrams only between the helper and the local core. The
WireGuard peer still carries UDP. Each media association owns its TCP stream.
"""
import asyncio
import struct

from socks_udp import pack_packet


class MediaProtocol(asyncio.StreamReaderProtocol):
    def __init__(self, reader):
        super().__init__(reader)
        self.writable = asyncio.Event()
        self.writable.set()
        self.closed_event = asyncio.Event()
        self.ready = asyncio.Event()
        self.stats = None
        self.write_timeout = 2

    def pause_writing(self):
        super().pause_writing()
        self.writable.clear()
        if self.stats is not None:
            self.stats['localMediaSendBackpressure'] = self.stats.get('localMediaSendBackpressure', 0) + 1

    def resume_writing(self):
        super().resume_writing()
        self.writable.set()

    def eof_received(self):
        super().eof_received()
        self.closed_event.set()
        self.ready.set()
        self.writable.set()
        return False  # A half-closed media association cannot deliver more replies.

    def connection_lost(self, error):
        super().connection_lost(error)
        self.closed_event.set()
        self.ready.set()
        self.writable.set()

    async def drain(self):
        if not self.writable.is_set():
            waiter = asyncio.create_task(self.writable.wait())
            try:
                done, _ = await asyncio.wait({waiter}, timeout=self.write_timeout)
                if not done:
                    if self.stats is not None:
                        self.stats['localMediaSendTimeouts'] = self.stats.get('localMediaSendTimeouts', 0) + 1
                    raise TimeoutError('LocalMediaWriteTimeout') from None
            finally:
                waiter.cancel()
                await asyncio.gather(waiter, return_exceptions=True)
        if self.closed_event.is_set():
            raise ConnectionError('Local media transport closed')


class SocksUOT:
    kind = 'uot2'

    def __init__(self, reader, writer, protocol):
        self.reader, self.writer, self.protocol = reader, writer, protocol
        self.target = None
        self.send_lock = asyncio.Lock()

    @classmethod
    async def create(cls, port, username, password):
        user, secret = username.encode('utf-8'), password.encode('utf-8')
        if not (1 <= len(user) <= 255 and 1 <= len(secret) <= 255):
            raise ValueError('Invalid SOCKS credential length')
        loop = asyncio.get_running_loop()
        reader = asyncio.StreamReader()
        protocol = MediaProtocol(reader)
        transport, _ = await loop.create_connection(lambda: protocol, '127.0.0.1', port)
        transport.set_write_buffer_limits(high=65536, low=16384)
        writer = asyncio.StreamWriter(transport, protocol, reader, loop)
        association = cls(reader, writer, protocol)
        try:
            writer.write(b'\x05\x01\x02')
            await protocol.drain()
            if await reader.readexactly(2) != b'\x05\x02':
                raise ConnectionError('SOCKS authentication method rejected')
            writer.write(b'\x01' + bytes([len(user)]) + user + bytes([len(secret)]) + secret)
            await protocol.drain()
            if await reader.readexactly(2) != b'\x01\x00':
                raise ConnectionError('SOCKS authentication rejected')
            magic = b'sp.v2.udp-over-tcp.arpa'
            writer.write(b'\x05\x01\x00\x03' + bytes([len(magic)]) + magic + b'\0\0')
            await protocol.drain()
            reply = await reader.readexactly(4)
            if reply[:3] != b'\x05\x00\x00':
                raise ConnectionError('SOCKS media connection rejected')
            length = {1: 4, 4: 16}.get(reply[3])
            if reply[3] == 3:
                length = (await reader.readexactly(1))[0]
            if length is None:
                raise ConnectionError('Invalid SOCKS reply address')
            await reader.readexactly(length + 2)
            return association
        except BaseException:
            transport.abort()
            # Consume a close error without suppressing the original failure.
            try:
                await writer.wait_closed()
            except Exception:
                pass
            raise

    async def send(self, target, payload):
        if len(payload) > 65507:
            raise ValueError('Media datagram too large')
        async with self.send_lock:
            await self.protocol.drain()
            if self.writer.is_closing():
                raise ConnectionError('Local media transport closed')
            if self.target is None:
                address = pack_packet(target, b'')[3:]
                self.target = tuple(target)
                self.writer.write(b'\x01' + address)
                self.protocol.ready.set()
            elif tuple(target) != self.target:
                raise ValueError('Media destination changed')
            # One write preserves each UDP boundary even when discovery and
            # ordinary media send concurrently. The next send honors pressure.
            self.writer.write(struct.pack('!H', len(payload)) + payload)

    async def receive(self):
        await self.protocol.ready.wait()
        if self.target is None or self.protocol.closed_event.is_set():
            raise ConnectionError('Local media transport closed')
        length = struct.unpack('!H', await self.reader.readexactly(2))[0]
        if length > 65507:
            self.writer.close()
            raise ConnectionError('Invalid media frame size')
        return self.target, await self.reader.readexactly(length)

    async def watch_control(self):
        # A separate reader would consume frame bytes or race receive().
        await self.protocol.closed_event.wait()
        raise ConnectionError('Local media transport closed')

    async def close(self):
        self.writer.close()
        try:
            await asyncio.wait_for(self.writer.wait_closed(), 1)
        except (OSError, asyncio.TimeoutError):
            self.writer.transport.abort()
