'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs'), path = require('node:path'), vm = require('node:vm'), crypto = require('node:crypto');
const source = fs.readFileSync(path.join(__dirname, '../source/runtime.template.js'), 'utf8')
  .replace('/*__CONFIG__*/null', JSON.stringify({relayPort:19884, nonce:'synthetic-test'}));

async function firstConnection(sender) {
  const roots = {}, listeners = new Set(), calls = [];
  let release, starts = 0;
  class NativeSocket { constructor(url) { this.url = url; calls.push(url); } }
  const ctx = vm.createContext({WebSocket:NativeSocket, URL, URLSearchParams, Proxy, Reflect, crypto,
    setTimeout, DiscordProtonStreamsNative:{ensureHelper() { starts++; return new Promise(resolve => {release = resolve;}); }}});
  ctx.window = ctx;
  ctx.Vencord = {Webpack:{findStore:() => ({getRTCConnections:() => roots,
    addChangeListener:fn => listeners.add(fn), removeChangeListener:fn => listeners.delete(fn)})}};
  vm.runInContext(source, ctx);
  ctx.__discordProtonStreams.automatico(true);
  const root = {context:'stream', state:'AWAITING_ENDPOINT', _socket:undefined, isStreamSender:() => sender};
  roots.stream = root;
  for (const listener of listeners) listener(); // STREAM_CREATE, before the endpoint arrives.
  const makeSocket = () => ({url:'wss://test-sfu.discord.media:8443/',
    createWebSocket() { this.webSocket = new ctx.WebSocket(this.url + '?v=10'); return this.webSocket; }});
  root._socket = makeSocket(); // Native connect() assigns and opens synchronously.
  root._socket.createWebSocket(); // No store notification between assignment and open.
  assert.equal(calls.length, 0, 'first connection must wait for the helper');
  assert.equal(starts, 1);
  release();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(calls.length, 1);
  assert.equal(new URL(calls[0]).origin, 'ws://127.0.0.1:19884');
  const session = new URL(calls[0]).searchParams.get('sid');
  root._socket.createWebSocket();
  assert.equal(starts,2,'reconnections must revalidate helper readiness');
  assert.equal(calls.length,1,'a reconnect must wait for the helper too');
  release();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(calls.length,2);
  assert.equal(new URL(calls.at(-1)).searchParams.get('sid'), session);
  const current = root._socket;
  const voice = makeSocket();
  voice.createWebSocket();
  assert.equal(calls.at(-1), 'wss://test-sfu.discord.media:8443/?v=10');
  ctx.__discordProtonStreams.remover();
  const descriptor = Object.getOwnPropertyDescriptor(root, '_socket');
  assert.equal(descriptor.value, current);
  assert.equal(descriptor.writable, true);
  assert.equal(descriptor.get, undefined);
  current.createWebSocket();
  assert.equal(calls.at(-1), 'wss://test-sfu.discord.media:8443/?v=10');
  assert.equal(listeners.size, 0);
}

async function failureRecovery() {
  const calls=[],timers=[],listeners=new Set();let now=0,starts=0,failFactory=true;
  class NativeSocket {constructor(url){this.url=url;calls.push(url);}}
  const ctx=vm.createContext({WebSocket:NativeSocket,URL,URLSearchParams,Proxy,Reflect,crypto,
    Date:{now:()=>now},setTimeout:fn=>timers.push(fn),DiscordProtonStreamsNative:{ensureHelper(){
      if(++starts===1)throw Error('SynchronousHelperFailure');return Promise.resolve();
    }}});ctx.window=ctx;
  const control={url:'wss://test-sfu.discord.media:8443/',createWebSocket(){
    if(failFactory)throw TypeError('SyntheticFactoryError');this.webSocket=new ctx.WebSocket(this.url+'?v=10');
  }};
  const root={context:'stream',state:'CONNECTING',_socket:control,isStreamSender:()=>false};
  ctx.Vencord={Webpack:{findStore:()=>({getRTCConnections:()=>({root}),addChangeListener:fn=>listeners.add(fn),removeChangeListener:fn=>listeners.delete(fn)})}};
  vm.runInContext(source,ctx);ctx.__discordProtonStreams.automatico(true);control.createWebSocket();
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(starts,1);assert.equal(ctx.__discordProtonStreams.status().lastError,'HelperUnavailable');
  now=1000;while(timers.length)timers.shift()();
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(starts,2);assert.equal(ctx.__discordProtonStreams.status().lastError,'TypeError');
  assert.equal(timers.length,0,'a factory exception must not create an endless helper retry loop');
  failFactory=false;control.createWebSocket();await new Promise(resolve=>setImmediate(resolve));
  assert.equal(starts,3);assert.equal(calls.length,1);
  ctx.__discordProtonStreams.remover();
}

async function removedWhileStarting(cancelNativeOnly=false) {
  const calls=[],listeners=new Set();let release;
  class NativeSocket {constructor(url){calls.push(url);}}
  const ctx=vm.createContext({WebSocket:NativeSocket,URL,URLSearchParams,Proxy,Reflect,crypto,setTimeout,
    DiscordProtonStreamsNative:{ensureHelper:()=>new Promise(resolve=>{release=resolve;})}});ctx.window=ctx;
  const root={context:'stream',state:'CONNECTING',isStreamSender:()=>false,_socket:{connectionState:1,url:'wss://test-sfu.discord.media/',
    createWebSocket(){return new ctx.WebSocket(this.url+'?v=10');}}};
  ctx.Vencord={Webpack:{findStore:()=>({getRTCConnections:()=>({root}),addChangeListener:fn=>listeners.add(fn),removeChangeListener:fn=>listeners.delete(fn)})}};
  vm.runInContext(source,ctx);ctx.__discordProtonStreams.automatico(true);root._socket.createWebSocket();
  if(cancelNativeOnly)root._socket.connectionState=0;
  else ctx.__discordProtonStreams.remover();
  release();await new Promise(resolve=>setImmediate(resolve));
  if(cancelNativeOnly)ctx.__discordProtonStreams.remover();
  assert.equal(calls.length,0,'unloading while starting must not open an orphan stream');assert.equal(listeners.size,0);
}

async function senderConnectedBeforeStoreNotification(){
  const listeners=new Set();let reconnects=0;
  class NativeSocket{constructor(url){this.url=url;}}
  const ctx=vm.createContext({WebSocket:NativeSocket,URL,URLSearchParams,Proxy,Reflect,crypto,setTimeout,
    DiscordProtonStreamsNative:{ensureHelper:async()=>{}}});ctx.window=ctx;
  const socket={connectionState:2,url:'wss://test-sfu.discord.media/',webSocket:{url:'wss://test-sfu.discord.media/?v=10'},
    createWebSocket(){this.webSocket=new ctx.WebSocket(this.url+'?v=10');}};
  const root={context:'stream',state:'RTC_CONNECTED',isStreamSender:()=>true,_socket:socket,reconnect(){reconnects++;socket.createWebSocket();}};
  ctx.Vencord={Webpack:{findStore:()=>({getRTCConnections:()=>({root}),addChangeListener:f=>listeners.add(f),removeChangeListener:f=>listeners.delete(f)})}};
  vm.runInContext(source,ctx);ctx.__discordProtonStreams.automatico(true);
  await new Promise(r=>setTimeout(r,25));
  assert.equal(reconnects,1,'a sender already connected before the store event must migrate once');
  assert.equal(new URL(socket.webSocket.url).origin,'ws://127.0.0.1:19884');
  for(const listener of listeners)listener();await new Promise(r=>setTimeout(r,10));
  assert.equal(reconnects,1,'healthy sender must not be reconnected again');ctx.__discordProtonStreams.remover();
}

function receiverBurst(){
  let now=0,nextId=0;const timers=new Map(),calls=[],roots={},listeners=new Set();
  class NativeSocket{constructor(url){this.url=url;calls.push({url,at:now});}}
  const ctx=vm.createContext({WebSocket:NativeSocket,URL,URLSearchParams,crypto,Date:{now:()=>now},
    setTimeout(fn,delay){const id=++nextId;timers.set(id,{fn,at:now+delay});return id;},clearTimeout:id=>timers.delete(id)});ctx.window=ctx;
  const make=(key,sender=false,context='stream')=>roots[key]={context,state:'CONNECTING',isStreamSender:()=>sender,
    _socket:{url:'wss://test.discord.media/',connectionState:1,createWebSocket(){this.webSocket=new ctx.WebSocket(this.url+'?v=10');}}};
  const a=make('a'),b=make('b'),c=make('c'),sender=make('sender',true),voice=make('voice',false,'default');
  ctx.Vencord={Webpack:{findStore:()=>({getRTCConnections:()=>roots,addChangeListener:f=>listeners.add(f),removeChangeListener:f=>listeners.delete(f)})}};
  vm.runInContext(source,ctx);ctx.__discordProtonStreams.automatico(true);
  a._socket.createWebSocket();b._socket.createWebSocket();c._socket.createWebSocket();
  b._socket.createWebSocket(); // A second callback must not bypass its pending slot.
  sender._socket.createWebSocket();voice._socket.createWebSocket();
  assert.equal(calls.length,3,'only the first receiver, sender and voice open immediately');
  function advance(to){now=to;for(const [id,timer] of [...timers])if(timer.at<=now){timers.delete(id);timer.fn();}}
  advance(749);assert.equal(calls.length,3);advance(750);assert.equal(calls.length,4);
  assert.equal(calls[3].at,750);advance(1500);assert.equal(calls.length,5);assert.equal(calls[4].at,1500);
  assert.equal(timers.size,0,'duplicate callbacks must not leave extra pending opens');
  const d=make('d');for(const f of listeners)f();d._socket.createWebSocket();
  delete roots.d;for(const f of listeners)f();advance(3000);assert.equal(calls.length,5,'closed queued receivers must not open');
  const e=make('e');for(const f of listeners)f();e._socket.createWebSocket();
  const g=make('g');for(const f of listeners)f();g._socket.createWebSocket();
  ctx.__discordProtonStreams.remover();const count=calls.length;advance(10000);
  assert.equal(calls.length,count,'unloading cancels pending opens');assert.equal(timers.size,0);
}

(async () => {
  await firstConnection(true);
  await firstConnection(false);
  await failureRecovery();
  await removedWhileStarting();
  await removedWhileStarting(true);
  await senderConnectedBeforeStoreNotification();
  receiverBurst();
  console.log('OK: sender/receiver startup and reconnections revalidate the helper; voice and cleanup preserved.');
})().catch(error => {console.error(error); process.exitCode = 1;});
