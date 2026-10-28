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

async function removedWhileStarting() {
  const calls=[],listeners=new Set();let release;
  class NativeSocket {constructor(url){calls.push(url);}}
  const ctx=vm.createContext({WebSocket:NativeSocket,URL,URLSearchParams,Proxy,Reflect,crypto,setTimeout,
    DiscordProtonStreamsNative:{ensureHelper:()=>new Promise(resolve=>{release=resolve;})}});ctx.window=ctx;
  const root={context:'stream',state:'CONNECTING',isStreamSender:()=>false,_socket:{url:'wss://test-sfu.discord.media/',
    createWebSocket(){return new ctx.WebSocket(this.url+'?v=10');}}};
  ctx.Vencord={Webpack:{findStore:()=>({getRTCConnections:()=>({root}),addChangeListener:fn=>listeners.add(fn),removeChangeListener:fn=>listeners.delete(fn)})}};
  vm.runInContext(source,ctx);ctx.__discordProtonStreams.automatico(true);root._socket.createWebSocket();
  ctx.__discordProtonStreams.remover();release();await new Promise(resolve=>setImmediate(resolve));
  assert.equal(calls.length,0,'unloading while starting must not open an orphan stream');assert.equal(listeners.size,0);
}

(async () => {
  await firstConnection(true);
  await firstConnection(false);
  await failureRecovery();
  await removedWhileStarting();
  console.log('OK: sender/receiver startup and reconnections revalidate the helper; voice and cleanup preserved.');
})().catch(error => {console.error(error); process.exitCode = 1;});
