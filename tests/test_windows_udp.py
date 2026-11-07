"""Exercise ICMP errors against real Windows sockets, without a VPN/Discord."""
import asyncio
import os
import socket
import time
import unittest
from unittest.mock import AsyncMock,patch

from socks_udp import PacketQueue,create_loopback_endpoint


class EndpointTests(unittest.IsolatedAsyncioTestCase):
    async def test_failed_endpoint_creation_closes_the_socket(self):
        loop=asyncio.get_running_loop();sockets=[]
        async def fail(factory,*,sock):
            sockets.append(sock)
            self.assertEqual(sock.getsockname()[0],'127.0.0.1')
            self.assertFalse(sock.getblocking())
            raise OSError('synthetic endpoint failure')
        with patch.object(loop,'create_datagram_endpoint',side_effect=fail):
            with self.assertRaises(OSError):await create_loopback_endpoint(PacketQueue)
        self.assertEqual(sockets[0].fileno(),-1)

    @unittest.skipUnless(os.name=='nt','Winsock ICMP behavior')
    async def test_configuration_failure_closes_socket_before_any_async_receive(self):
        loop=asyncio.get_running_loop()
        with patch('windows_udp.suppress_udp_resets',side_effect=OSError('synthetic ioctl failure')) as configure, \
                patch.object(loop,'create_datagram_endpoint',new=AsyncMock()) as create:
            with self.assertRaises(OSError):await create_loopback_endpoint(PacketQueue)
        self.assertEqual(configure.call_args.args[0].fileno(),-1)
        create.assert_not_awaited()

    @unittest.skipUnless(os.name=='nt','Winsock ICMP behavior')
    async def test_proactor_receives_after_repeated_sends_to_a_closed_port(self):
        # A Discord reconnect closes its old local UDP port while replies are
        # still in flight. These stale sends must not kill the receive loop.
        with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as closed:
            closed.bind(('127.0.0.1',0));unreachable=closed.getsockname()
        errors=[]
        class Protocol(PacketQueue):
            def error_received(self,error):errors.append(error)
            def datagram_received(self,data,source):
                super().datagram_received(data,source)
                # Allow the previous send's ICMP to arrive before Proactor
                # starts its next receive, reproducing the synchronous error.
                time.sleep(.01)
        transport,protocol=await create_loopback_endpoint(Protocol)
        sender,sender_protocol=await create_loopback_endpoint(PacketQueue)
        try:
            self.assertIsInstance(asyncio.get_running_loop(),asyncio.ProactorEventLoop)
            for index in range(25):
                transport.sendto(b'stale-reply',unreachable)
                await asyncio.sleep(.01)
                payload=index.to_bytes(4,'big')+bytes(1196)
                sender.sendto(payload,transport.get_extra_info('sockname'))
                received,peer=await asyncio.wait_for(protocol.queue.get(),1)
                self.assertEqual(received,payload)
                # Confirm both directions still work on the SAME endpoints.
                transport.sendto(received,peer)
                echo,_=await asyncio.wait_for(sender_protocol.queue.get(),1)
                self.assertEqual(echo,payload)
            self.assertEqual(errors,[])
        finally:
            transport.abort();sender.abort();await asyncio.sleep(0)


@unittest.skipUnless(os.name=='nt','Winsock ICMP behavior')
class WinsockTests(unittest.TestCase):
    def test_disable_reset_notifications_on_real_socket(self):
        from windows_udp import suppress_udp_resets
        with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as closed:
            closed.bind(('127.0.0.1',0));unreachable=closed.getsockname()
        # Positive control proves this host actually delivers ICMP errors.
        with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as control:
            control.settimeout(.3);control.sendto(b'stale-reply',unreachable)
            try:control.recvfrom(65535)
            except ConnectionResetError:pass
            except socket.timeout:self.skipTest('host does not deliver loopback ICMP errors')
            else:self.fail('unexpected response from closed port')
        with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as protected:
            suppress_udp_resets(protected);protected.bind(('127.0.0.1',0));protected.settimeout(.1)
            protected.sendto(b'stale-reply',unreachable)
            with self.assertRaises(socket.timeout):protected.recvfrom(65535)
            with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as sender:
                sender.sendto(b'still-alive',protected.getsockname())
                self.assertEqual(protected.recvfrom(65535)[0],b'still-alive')


if __name__=='__main__':unittest.main()
