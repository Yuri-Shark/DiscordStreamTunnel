"""Offline media stress probe through two real userspace WireGuard cores.

Uses generated test keys, loopback peers and a local echo server. Never loads
the installed account configuration, changes system routes or contacts Proton.
"""
import argparse
import asyncio
import json
import multiprocessing as mp
import os
import re
from pathlib import Path
import socket
import struct
import subprocess
import sys
import tempfile
import time

from benchmark_udp import native_worker
from stream_relay import MediaBridge


def echo_worker(pipe):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4 * 1024 * 1024)
    sock.bind(('127.0.0.1', 0))
    sock.settimeout(.2)
    pipe.send(sock.getsockname()[1])
    received = sent = 0
    while not pipe.poll():
        try:
            packet, peer = sock.recvfrom(65535)
        except socket.timeout:
            continue
        received += 1
        sock.sendto(packet, peer)
        sent += 1
    sock.close()
    pipe.send({'received': received, 'sent': sent})


def link_worker(pipe, server_port, client_port):
    """Count encrypted datagrams at the WireGuard boundary, without decrypting."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4 * 1024 * 1024)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 4 * 1024 * 1024)
    sock.bind(('127.0.0.1', 0))
    sock.settimeout(.2)
    pipe.send(sock.getsockname()[1])
    counts = {'fromClient': 0, 'fromServer': 0, 'unexpectedSource': 0}
    counters = {'fromClient': {}, 'fromServer': {}}
    while not pipe.poll():
        try:
            packet, peer = sock.recvfrom(65535)
        except socket.timeout:
            continue
        if not client_port and peer[0] == '127.0.0.1' and peer[1] != server_port and packet[:4] == b'\x01\0\0\0':
            client_port = peer[1]
        if peer[0] != '127.0.0.1' or peer[1] not in (server_port, client_port):
            counts['unexpectedSource'] += 1
            continue
        client = peer[1] == client_port
        if len(packet) > 32 and packet[:4] == struct.pack('<I', 4):
            counts['fromClient' if client else 'fromServer'] += 1
        if len(packet) >= 32 and packet[:4] == struct.pack('<I', 4):
            epoch, counter = struct.unpack_from('<IQ', packet, 4)
            counters['fromClient' if client else 'fromServer'].setdefault(epoch, set()).add(counter)
        sock.sendto(packet, ('127.0.0.1', server_port if client else client_port))
    sock.close()
    for direction, epochs in counters.items():
        counts[direction + 'CounterGaps'] = sum(max(seen)-min(seen)+1-len(seen) for seen in epochs.values() if seen)
    pipe.send(counts)


class CorePair:
    def __init__(self, executable, workers=2, goprocs=2, mtu=1280, observe_link=False, single_peer=False, core_errors=False):
        self.executable = str(Path(executable).resolve())
        self.workers, self.goprocs, self.mtu = workers, goprocs, mtu
        self.processes = []
        self.job = None
        self.directory = None
        self.observe_link = observe_link
        self.link_process = None
        self.link_stats = {}
        self.single_peer, self.log_errors = single_peer, core_errors
        self.core_errors = {}

    def keypair(self):
        result = subprocess.run([self.executable, 'generate', 'wg-keypair'],
            capture_output=True, text=True, check=True,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        return {key.strip().lower().replace(' ', ''): value.strip()
                for key, value in (line.split(':', 1) for line in result.stdout.splitlines() if ':' in line)}

    def __enter__(self):
        try:
            self.directory = tempfile.TemporaryDirectory(prefix='dts-synthetic-wg-')
            if os.name == 'nt':
                from windows_process import ChildJob
                self.job = ChildJob()
            server_keys, client_keys = self.keypair(), self.keypair()
            reservations = []
            try:
                for kind in (socket.SOCK_DGRAM, socket.SOCK_DGRAM, socket.SOCK_STREAM):
                    sock = socket.socket(socket.AF_INET, kind)
                    reservations.append(sock)
                    sock.bind(('127.0.0.1', 0))
                server_port, client_port, self.socks_port = [s.getsockname()[1] for s in reservations]
            finally:
                for sock in reservations:
                    sock.close()
            link_port = None
            if self.observe_link:
                self.link_read, self.link_write = mp.get_context('spawn').Pipe()
                self.link_process = mp.get_context('spawn').Process(target=link_worker,
                    args=(self.link_write, server_port, 0 if self.single_peer else client_port))
                self.link_process.start()
                if not self.link_read.poll(5):
                    raise TimeoutError('Synthetic WireGuard link did not start')
                link_port = self.link_read.recv()
            def endpoint(private, public, listen, remote, address, allowed):
                return {'type': 'wireguard', 'tag': 'test-wg', 'system': False,
                    'mtu': self.mtu, 'address': [address], 'private_key': private,
                    'listen_port': listen, 'workers': self.workers,
                    'peers': [{'address': '127.0.0.1', 'port': remote,
                        'public_key': public, 'allowed_ips': [allowed]}]}
            server = {'log': {'disabled': True},
                'endpoints': [endpoint(server_keys['privatekey'], client_keys['publickey'],
                    server_port, link_port or client_port, '10.77.0.1/30', '10.77.0.2/32')],
                'outbounds': [{'type': 'direct', 'tag': 'test-echo'}],
                'route': {'rules': [{'ip_cidr': ['198.18.0.1/32'], 'network': 'udp',
                    'action': 'route', 'outbound': 'test-echo', 'override_address': '127.0.0.1'},
                    {'action': 'reject'}]}}
            client = {'log': {'disabled': True},
                'inbounds': [{'type': 'socks', 'listen': '127.0.0.1', 'listen_port': self.socks_port,
                    'users': [{'username': 'discord-stream', 'password': 'synthetic'}]}],
                'endpoints': [endpoint(client_keys['privatekey'], server_keys['publickey'],
                    0 if self.single_peer else client_port, link_port or server_port, '10.77.0.2/30', '0.0.0.0/0')],
                'route': {'final': 'test-wg'}}
            for name, config in [('server', server), ('client', client)]:
                if self.log_errors:
                    config['log'] = {'level': 'error', 'output': str(Path(self.directory.name) / (name + '.log'))}
                config_path = Path(self.directory.name) / (name + '.json')
                config_path.write_text(json.dumps(config), encoding='utf-8')
                subprocess.run([self.executable, 'check', '-c', str(config_path)],
                    capture_output=True, check=True,
                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                process = subprocess.Popen([self.executable, 'run', '-c', str(config_path)],
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    env={**os.environ, 'GOMAXPROCS': str(self.goprocs), 'GOMEMLIMIT': '128MiB'},
                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                self.processes.append(process)
                if self.job is not None:
                    self.job.assign(process)
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def __exit__(self, *_):
        try:
            for process in self.processes:
                if process.poll() is None:
                    process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=3)
        finally:
            if self.job is not None:
                self.job.close()
            if self.link_process is not None:
                self.link_read.send(True)
                self.link_process.join(3)
                if self.link_process.is_alive():
                    self.link_process.terminate()
                    self.link_process.join(3)
                if self.link_read.poll():
                    self.link_stats.update(self.link_read.recv())
                self.link_read.close()
                self.link_write.close()
            if self.directory is not None:
                if self.log_errors:
                    for name in ('server', 'client'):
                        path = Path(self.directory.name) / (name + '.log')
                        raw = path.read_text(encoding='utf-8') if path.exists() else ''
                        self.core_errors[name] = {'lines': len(raw.splitlines()),
                            'failedDataSends': len(re.findall('failed to send data packets', raw, re.I)),
                            'bufferExhaustion': len(re.findall('no buffer space|10055|WSAENOBUFS', raw, re.I))}
                self.directory.cleanup()

    async def ready(self):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if any(process.poll() is not None for process in self.processes):
                raise RuntimeError('Synthetic WireGuard core exited')
            try:
                _, writer = await asyncio.open_connection('127.0.0.1', self.socks_port)
            except OSError:
                await asyncio.sleep(.05)
            else:
                writer.close()
                await writer.wait_closed()
                return
        raise TimeoutError('Synthetic WireGuard SOCKS did not start')


async def run(args):
    ctx = mp.get_context('spawn')
    read_echo, write_echo = ctx.Pipe()
    echo = ctx.Process(target=echo_worker, args=(write_echo,))
    echo.start()
    bridges, worker, result = [], None, None
    try:
        if not await asyncio.to_thread(read_echo.poll, 5):
            raise TimeoutError('Local echo did not start')
        echo_port = read_echo.recv()
        with CorePair(args.core, args.workers, args.goprocs, args.mtu, getattr(args, 'observe_link', False),
                getattr(args, 'single_peer', False), getattr(args, 'core_errors', False)) as cores:
            await cores.ready()
            stats = dict(udpQueueDrops=0, udpUpBytes=0, udpDownBytes=0, udpUpPackets=0, udpDownPackets=0)
            try:
                for _ in range(args.streams):
                    bridges.append(await MediaBridge.create({'socksPort': cores.socks_port, 'nonce': 'synthetic',
                        'mediaTransport': getattr(args, 'transport', 'udp')},
                        ('198.18.0.1', echo_port), stats))
                read_native, write_native = ctx.Pipe()
                worker = ctx.Process(target=native_worker, args=(write_native,
                    [bridge.local_port for bridge in bridges], args.pps, args.seconds, args.burst, True))
                worker.start()
                started = time.process_time()
                if not await asyncio.to_thread(read_native.poll, args.seconds + 15):
                    raise TimeoutError('Real WireGuard benchmark timed out')
                result = read_native.recv()
                result.update(streams=args.streams, ppsPerStream=args.pps, durationSeconds=args.seconds,
                    burst=args.burst, workers=args.workers, goprocs=args.goprocs, mtu=args.mtu,
                    realWireGuard=True, accountConfigurationUsed=False, transport=getattr(args, 'transport', 'udp'),
                    clientSinglePeer=cores.single_peer,
                    eventLoop=type(asyncio.get_running_loop()).__name__,
                    relayCpuSeconds=round(time.process_time() - started, 2), stats=stats)
                if cores.observe_link:
                    result['wireguardBoundary'] = cores.link_stats
                if cores.log_errors:
                    result['coreErrors'] = cores.core_errors
                return result
            finally:
                if worker is not None:
                    await asyncio.to_thread(worker.join, 3)
                    if worker.is_alive():
                        worker.terminate()
                        await asyncio.to_thread(worker.join, 3)
                for bridge in bridges:
                    await bridge.close()
    finally:
        read_echo.send(True)
        await asyncio.to_thread(echo.join, 3)
        if echo.is_alive():
            echo.terminate()
            await asyncio.to_thread(echo.join, 3)
        if result is not None and read_echo.poll():
            result['echoPackets'] = read_echo.recv()
        read_echo.close()
        write_echo.close()


if __name__ == '__main__':
    mp.freeze_support()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--core', type=Path, required=True, help='Existing trusted sing-box executable')
    parser.add_argument('--streams', type=int, default=3)
    parser.add_argument('--pps', type=int, default=1000)
    parser.add_argument('--seconds', type=float, default=10)
    parser.add_argument('--burst', type=int, default=32)
    parser.add_argument('--workers', type=int, default=2)
    parser.add_argument('--goprocs', type=int, default=2)
    parser.add_argument('--mtu', type=int, default=1280)
    parser.add_argument('--observe-link', action='store_true', help='Count encrypted datagrams at a local UDP forwarding hop')
    parser.add_argument('--transport', choices=['udp', 'uot2'], default='udp')
    parser.add_argument('--single-peer', action='store_true', help='Use the same ephemeral single-peer bind as production')
    parser.add_argument('--core-errors', action='store_true', help='Count synthetic-core errors without exporting log text')
    args = parser.parse_args()
    if not (args.core.is_file() and 1 <= args.streams <= 8 and 1 <= args.pps <= 100000
            and 0 < args.seconds <= 300 and 1 <= args.burst <= 1024
            and 1 <= args.workers <= 64 and 1 <= args.goprocs <= 64 and 1280 <= args.mtu <= 1500):
        parser.error('Invalid core path or benchmark parameters')
    if os.name == 'nt':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    print(json.dumps(asyncio.run(run(args))))
