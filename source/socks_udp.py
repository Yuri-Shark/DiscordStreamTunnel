"""RFC 1928 UDP association on a loopback authenticated SOCKS5 server."""
import asyncio
import ipaddress
import socket
import struct

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

class PacketQueue(asyncio.DatagramProtocol):
    def __init__(self): self.queue=asyncio.Queue(maxsize=1024);self.drops=0;self.stats=None
    def datagram_received(self,data,source):
        try: self.queue.put_nowait((data,source))
        except asyncio.QueueFull:
            self.drops+=1
            if self.stats is not None:self.stats['udpReceiveQueueDrops']=self.stats.get('udpReceiveQueueDrops',0)+1

class SocksUDP:
    def __init__(self,reader,writer,transport,protocol,relay):
        self.reader,self.writer,self.transport,self.protocol,self.relay=reader,writer,transport,protocol,relay
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
            transport,protocol=await asyncio.get_running_loop().create_datagram_endpoint(PacketQueue,local_addr=('127.0.0.1',0))
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
        self.transport.sendto(pack_packet(target,data),self.relay)
    async def watch_control(self):
        # RFC 1928: closing TCP also terminates the UDP association. UDP
        # receive alone would otherwise wait forever after a core failure.
        await self.reader.read(1)
        raise ConnectionError('SOCKS UDP association closed')
    async def receive(self):
        while True:
            packet,source=await self.protocol.queue.get()
            if source==self.relay:
                return unpack_packet(packet)
    async def close(self):
        self.transport.close();self.writer.close()
        try: await self.writer.wait_closed()
        except Exception: pass
