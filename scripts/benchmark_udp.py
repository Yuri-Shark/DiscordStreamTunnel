"""Offline UDP relay stress probe. No production config or Internet access."""
import asyncio
import argparse
import json
import multiprocessing as mp
import os
import select
import socket
import struct
import sys
import time
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'source'),str(ROOT/'source/vendor')]
if os.environ.get('DTS_BENCHMARK_SOURCE'):
    sys.path.insert(0,os.environ['DTS_BENCHMARK_SOURCE'])
from stream_relay import MediaBridge


def echo_worker(pipe):
    sock=socket.socket(socket.AF_INET,socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET,socket.SO_RCVBUF,4*1024*1024)
    sock.bind(('127.0.0.1',0));sock.settimeout(.2)
    pipe.send(sock.getsockname()[1])
    while not pipe.poll():
        try:packet,addr=sock.recvfrom(65535);sock.sendto(packet,addr)
        except socket.timeout:pass
    sock.close()


def native_worker(pipe,ports,pps,duration,burst):
    socks=[]
    for port in ports:
        sock=socket.socket(socket.AF_INET,socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET,socket.SO_RCVBUF,4*1024*1024)
        sock.bind(('127.0.0.1',0));sock.setblocking(False);socks.append(sock)
    sent=[0]*len(socks);received=[set() for _ in socks];delays=[]
    begin=time.perf_counter();next_send=begin;end=begin+duration
    while time.perf_counter()<end+1:
        now=time.perf_counter()
        if now>=next_send and now<end:
            for index,sock in enumerate(socks):
                for _ in range(burst):
                    packet=struct.pack('!Id',sent[index],time.perf_counter())+bytes(1188)
                    try:sock.sendto(packet,('127.0.0.1',ports[index]));sent[index]+=1
                    except BlockingIOError:break
            next_send=max(next_send+burst/pps,now)
        ready,_,_=select.select(socks,[],[],min(.001,max(0,next_send-time.perf_counter())) if now<end else .001)
        for sock in ready:
            index=socks.index(sock)
            for _ in range(256):
                try:packet,_=sock.recvfrom(65535)
                except BlockingIOError:break
                seq,at=struct.unpack('!Id',packet[:12]);received[index].add(seq)
                delays.append((time.perf_counter()-at)*1000)
    delays.sort()
    pipe.send({'sent':sent,'received':[len(x) for x in received],
               'latencyMs':{str(p):round(delays[min(len(delays)-1,int(len(delays)*p/100))],2) for p in [50,95,99,100]} if delays else {}})
    for sock in socks:sock.close()


async def run(streams,pps,duration,burst):
    ctx=mp.get_context('spawn');echo_read,echo_write=ctx.Pipe()
    echo=ctx.Process(target=echo_worker,args=(echo_write,));echo.start()
    echo_port=await asyncio.to_thread(echo_read.recv)
    controls=set()
    async def control(reader,writer):
        controls.add(writer)
        try:
            await reader.readexactly(3);writer.write(b'\x05\x02');await writer.drain()
            await reader.readexactly(1)
            await reader.readexactly((await reader.readexactly(1))[0])
            await reader.readexactly((await reader.readexactly(1))[0])
            writer.write(b'\x01\x00');await writer.drain()
            await reader.readexactly(10)
            writer.write(b'\x05\x00\x00\x01'+socket.inet_aton('127.0.0.1')+struct.pack('!H',echo_port));await writer.drain()
            await reader.read()
        finally:writer.close();controls.discard(writer)
    server=await asyncio.start_server(control,'127.0.0.1',0)
    stats=dict(udpQueueDrops=0,udpUpBytes=0,udpDownBytes=0,udpUpPackets=0,udpDownPackets=0)
    bridges=[];worker=None
    try:
        for _ in range(streams):
            bridges.append(await MediaBridge.create({'socksPort':server.sockets[0].getsockname()[1],'nonce':'synthetic'},('8.8.8.8',50000),stats))
        read,write=ctx.Pipe()
        worker=ctx.Process(target=native_worker,args=(write,[b.local_port for b in bridges],pps,duration,burst));worker.start()
        started=time.process_time()
        if not await asyncio.to_thread(read.poll,duration+15):raise TimeoutError('Offline UDP benchmark timed out')
        result=read.recv()
        result.update(streams=streams,ppsPerStream=pps,durationSeconds=duration,burst=burst,
                      python=sys.version.split()[0],eventLoop=type(asyncio.get_running_loop()).__name__,
                      relayCpuSeconds=round(time.process_time()-started,2),stats=stats)
        return result
    finally:
        if worker is not None:
            await asyncio.to_thread(worker.join,3)
            if worker.is_alive():worker.terminate();await asyncio.to_thread(worker.join,3)
        for bridge in bridges:await bridge.close()
        server.close();await server.wait_closed()
        for writer in list(controls):writer.close()
        echo_read.send(True);await asyncio.to_thread(echo.join,3)
        if echo.is_alive():echo.terminate();await asyncio.to_thread(echo.join,3)


if __name__=='__main__':
    mp.freeze_support()
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--streams',type=int,default=3)
    parser.add_argument('--pps',type=int,default=1000,help='1200-byte datagrams per second per stream')
    parser.add_argument('--seconds',type=float,default=10)
    parser.add_argument('--burst',type=int,default=32)
    parser.add_argument('--output',type=Path)
    parser.add_argument('--selector',action='store_true',help='Compare the Windows selector UDP event loop')
    args=parser.parse_args()
    if not (1<=args.streams<=8 and 1<=args.pps<=100000 and 0<args.seconds<=300 and 1<=args.burst<=1024):
        parser.error('Expected 1-8 streams, 1-100000 pps, 0-300 seconds and burst 1-1024')
    runner=getattr(asyncio,'Runner',None)
    if args.selector and os.name=='nt' and runner is not None:
        with runner(loop_factory=asyncio.SelectorEventLoop) as instance:
            report=instance.run(run(args.streams,args.pps,args.seconds,args.burst))
    else:
        if args.selector and os.name=='nt':asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        report=asyncio.run(run(args.streams,args.pps,args.seconds,args.burst))
    if args.output:
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(report))
