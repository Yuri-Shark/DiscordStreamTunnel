"""RFC 1928 UDP association on a loopback authenticated SOCKS5 server."""
import asyncio
import ipaddress
import os
import socket
import struct

# A keyframe can arrive as hundreds of datagrams while the game occupies a
# CPU core. The OS queue is separate from PacketQueue; its default can be only
# 64 KiB even when our Python queue reports no drops. Keep both queues bounded.
UDP_BUFFER_BYTES=4*1024*1024

async def create_loopback_endpoint(factory):
    # Configure the real socket before asyncio starts its first overlapped
    # receive. TransportSocket doesn't expose ioctl on all Python versions.
    sock=socket.socket(socket.AF_INET,socket.SOCK_DGRAM)
    try:
        if os.name=='nt':
            from windows_udp import suppress_udp_resets
            suppress_udp_resets(sock)
        sock.setblocking(False);sock.bind(('127.0.0.1',0))
        return await asyncio.get_running_loop().create_datagram_endpoint(factory,sock=sock)
    except BaseException:
        sock.close();raise


def configure_datagram_socket(transport):
    sock=transport.get_extra_info('socket')
    if sock is None:return
    for option in (socket.SO_RCVBUF,socket.SO_SNDBUF):
        try:sock.setsockopt(socket.SOL_SOCKET,option,UDP_BUFFER_BYTES)
        except OSError:pass  # Some platforms cap buffers; retain their limit.

def pack_packet(target,payload):
    host,port=target
    address=ipaddress.ip_address(host)
    atyp=1 if address.version==4 else 4
    return b'\0\0\0'+bytes([atyp])+address.packed+struct.pack('!H',port)+payload

def unpack_packet(data):
    if len(data)<4 or data[:3]!=b'\0\0\0': raise ValueError('Invalid or fragmented SOCKS datagram')
    atyp=data[3]
    length={1:4,4:16}.get(atyp)
    if length is None or len(data)<6+length: raise ValueError('Invalid SOCKS address')
    host=str(ipaddress.ip_address(data[4:4+length]))
    return (host,struct.unpack('!H',data[4+length:6+length])[0]),data[6+length:]

class DatagramFlowControl(asyncio.DatagramProtocol):
    def __init__(self):
        self.writable=asyncio.Event();self.writable.set();self.closed=False;self.stats=None
        self.write_timeout=2
    def pause_writing(self):
        # asyncio's UDP transport has another queue beyond our bounded queues.
        # In particular, Windows overlapped sends can accumulate there even
        # when sendto() returns immediately. Honor its high/low water marks.
        self.writable.clear()
        if self.stats is not None:self.stats['udpSendBackpressure']=self.stats.get('udpSendBackpressure',0)+1
    def resume_writing(self):self.writable.set()
    def connection_lost(self,error):
        self.closed=True;self.writable.set()
    async def drain(self):
        if not self.writable.is_set():
            # wait_for can swallow cancellation racing with resume on Python
            # 3.10. Keep cancellation on the sending task itself with wait().
            waiter=asyncio.create_task(self.writable.wait())
            try:
                done,_=await asyncio.wait({waiter},timeout=self.write_timeout)
                if not done:
                    if self.stats is not None:self.stats['udpSendTimeouts']=self.stats.get('udpSendTimeouts',0)+1
                    raise TimeoutError('LocalUDPWriteTimeout') from None
            finally:
                waiter.cancel();await asyncio.gather(waiter,return_exceptions=True)
        if self.closed:raise ConnectionError('Local UDP transport closed')


class PacketQueue(DatagramFlowControl):
    def __init__(self):
        super().__init__();self.queue=asyncio.Queue(maxsize=1024);self.drops=0
    def connection_made(self,transport):configure_datagram_socket(transport)
    def error_received(self,error):
        if self.stats is not None:self.stats['udpSocketErrors']=self.stats.get('udpSocketErrors',0)+1
    def datagram_received(self,data,source):
        try: self.queue.put_nowait((data,source))
        except asyncio.QueueFull:
            self.drops+=1
            if self.stats is not None:self.stats['udpReceiveQueueDrops']=self.stats.get('udpReceiveQueueDrops',0)+1

class SocksUDP:
    def __init__(self,reader,writer,transport,protocol,relay):
        self.reader,self.writer,self.transport,self.protocol,self.relay=reader,writer,transport,protocol,relay
        # A media association normally uses one endpoint for its lifetime.
        # Re-parsing numeric IP addresses per datagram consumes the relay's
        # event-loop budget under sustained video/keyframe traffic. Cache only
        # one validated header in each direction, never media or an endpoint map.
        self.send_target=None;self.send_header=None
        self.receive_target=None;self.receive_header=None
    @classmethod
    async def create(cls,port,username,password):
        reader,writer=await asyncio.open_connection('127.0.0.1',port)
        transport=None
        try:
            writer.write(b'\5\1\2');await writer.drain()
            if await reader.readexactly(2)!=b'\5\2': raise ConnectionError('SOCKS authentication unavailable')
            user,secret=username.encode(),password.encode()
            if not 1<=len(user)<=255 or not 1<=len(secret)<=255: raise ValueError('SOCKS credentials length')
            writer.write(b'\1'+bytes([len(user)])+user+bytes([len(secret)])+secret);await writer.drain()
            if await reader.readexactly(2)!=b'\1\0': raise ConnectionError('SOCKS authentication failed')
            transport,protocol=await create_loopback_endpoint(PacketQueue)
            writer.write(b'\5\3\0\1'+socket.inet_aton('127.0.0.1')+struct.pack('!H',transport.get_extra_info('sockname')[1]));await writer.drain()
            head=await reader.readexactly(4)
            if head[:3]!=b'\5\0\0': raise ConnectionError('SOCKS UDP association refused')
            if head[3]==1: host=socket.inet_ntop(socket.AF_INET,await reader.readexactly(4))
            elif head[3]==4: host=socket.inet_ntop(socket.AF_INET6,await reader.readexactly(16))
            else: raise ValueError('Unsupported SOCKS relay address')
            relay_port=struct.unpack('!H',await reader.readexactly(2))[0]
            if host=='0.0.0.0': host='127.0.0.1'
            if host!='127.0.0.1': raise ValueError('SOCKS relay must remain on loopback')
            return cls(reader,writer,transport,protocol,(host,relay_port))
        except BaseException:
            writer.close()
            if transport: transport.close()
            raise
    async def send(self,target,data):
        await self.protocol.drain()
        if target!=self.send_target:
            self.send_header=pack_packet(target,b'');self.send_target=tuple(target)
        self.transport.sendto(self.send_header+data,self.relay)
    async def watch_control(self):
        # RFC 1928: closing TCP also terminates the UDP association. UDP
        # receive alone would otherwise wait forever after a core failure.
        await self.reader.read(1)
        raise ConnectionError('SOCKS UDP association closed')
    async def receive(self):
        while True:
            packet,source=await self.protocol.queue.get()
            if source==self.relay:
                header=self.receive_header
                if header is not None and packet.startswith(header):
                    return self.receive_target,packet[len(header):]
                target,payload=unpack_packet(packet)
                self.receive_header=packet[:len(packet)-len(payload)];self.receive_target=target
                return target,payload
    async def close(self):
        self.transport.close();self.writer.close()
        try: await asyncio.wait_for(self.writer.wait_closed(),1)
        except Exception: pass
