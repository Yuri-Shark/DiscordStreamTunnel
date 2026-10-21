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

(async () => {
  await firstConnection(true);
  await firstConnection(false);
  console.log('OK: first sender and receiver connections wait for the tunnel; voice and cleanup preserved.');
})().catch(error => {console.error(error); process.exitCode = 1;});
