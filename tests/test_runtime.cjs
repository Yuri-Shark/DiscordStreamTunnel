const assert=require('node:assert/strict');
const fs=require('node:fs'),vm=require('node:vm'),path=require('node:path'),crypto=require('node:crypto');
const source=fs.readFileSync(path.join(__dirname,'..','source','runtime.template.js'),'utf8').replace('/*__CONFIG__*/null',JSON.stringify({relayPort:19884,nonce:'synthetic-test'}));
const calls=[],timers=[],listeners=new Set();
class WebSocket {constructor(url){this.url=url;calls.push(url);}}
const ctx=vm.createContext({WebSocket,URL,URLSearchParams,Proxy,Reflect,crypto,setTimeout:f=>timers.push(f)});ctx.window=ctx;
function socket(){return{url:'wss://test-sfu.discord.media:8443/',createWebSocket(){this.webSocket=new ctx.WebSocket(this.url+'?v=9');return this.webSocket;}};}
const voiceSocket=socket(),receiverSocket=socket(),senderSocket=socket();
const originalVoice=voiceSocket.createWebSocket;
const originalReceiver=receiverSocket.createWebSocket,originalSender=senderSocket.createWebSocket;
let reconnects=0;
const receiver={context:'stream',state:'RTC_CONNECTED',_socket:receiverSocket,isStreamSender:()=>false,reconnect(){reconnects++;receiverSocket.createWebSocket();}};
const sender={context:'stream',state:'RTC_CONNECTED',_socket:senderSocket,isStreamSender:()=>true,reconnect(){senderSocket.createWebSocket();}};
const voice={context:'default',state:'RTC_CONNECTED',_socket:voiceSocket,isStreamSender:()=>false};
const roots={receiver,sender,voice};
const store={getRTCConnections:()=>roots,addChangeListener:f=>listeners.add(f),removeChangeListener:f=>listeners.delete(f)};
ctx.Vencord={Webpack:{findStore:()=>store,Common:{FluxDispatcher:{dispatch(){}}}}};
vm.runInContext(source,ctx);
assert.equal(voiceSocket.createWebSocket,originalVoice);
receiverSocket.createWebSocket();
const first=new URL(calls.at(-1));
assert.equal(first.origin,'ws://127.0.0.1:19884');
assert.equal(first.searchParams.get('target'),'wss://test-sfu.discord.media:8443/?v=9');
receiverSocket.createWebSocket();
assert.equal(new URL(calls.at(-1)).searchParams.get('sid'),first.searchParams.get('sid'));
senderSocket.createWebSocket();
assert.equal(new URL(calls.at(-1)).origin,'ws://127.0.0.1:19884');
assert.notEqual(new URL(calls.at(-1)).searchParams.get('sid'),first.searchParams.get('sid'));
assert.equal(ctx.WebSocket,WebSocket);
voiceSocket.createWebSocket();assert.equal(calls.at(-1),'wss://test-sfu.discord.media:8443/?v=9');
receiverSocket.url='wss://untrusted.example/';receiverSocket.createWebSocket();assert.equal(calls.at(-1),'wss://untrusted.example/?v=9');
receiverSocket.url='wss://test-sfu.discord.media:8443/';
ctx.__discordProtonStreams.testarRecepcaoAtual();assert.equal(reconnects,1);
ctx.__discordProtonStreams.automatico(true);
while(timers.length)timers.shift()();
assert.equal(reconnects,1);
delete roots.sender;for(const listener of listeners)listener();assert.equal(senderSocket.createWebSocket,originalSender);
ctx.__discordProtonStreams.remover();
assert.equal(receiverSocket.createWebSocket,originalReceiver);assert.equal(listeners.size,0);assert.equal(ctx.WebSocket,WebSocket);
console.log('OK: stream-only routing, unchanged main voice, stable resume identity, receivers-only test, automatic renewal and cleanup.');
async function lazyStartupTest(){
  let ready,starts=0,renewed=0;
  const delayed=[],created=[];
  class NativeSocket {constructor(url){this.url=url;created.push(url);}}
  const lazy=vm.createContext({WebSocket:NativeSocket,URL,URLSearchParams,Proxy,Reflect,crypto,setTimeout:f=>delayed.push(f),DiscordProtonStreamsNative:{ensureHelper(){starts++;return starts===1?new Promise(r=>{ready=r;}):Promise.resolve();}}});lazy.window=lazy;
  const control={url:'wss://test-sfu.discord.media:8443/',webSocket:{url:'wss://test-sfu.discord.media:8443/?v=9'},createWebSocket(){this.webSocket=new lazy.WebSocket(this.url+'?v=9');}};
  const root={context:'stream',state:'RTC_CONNECTED',_socket:control,isStreamSender:()=>false,reconnect(){renewed++;control.createWebSocket();}};
  const changes=new Set();
  lazy.Vencord={Webpack:{findStore:()=>({getRTCConnections:()=>({root}),addChangeListener:f=>changes.add(f),removeChangeListener:f=>changes.delete(f)})}};
  vm.runInContext(source,lazy);lazy.__discordProtonStreams.automatico(true);
  assert.equal(starts,1);assert.equal(renewed,0);assert.equal(delayed.length,0);
  ready();await new Promise(r=>setImmediate(r));
  while(delayed.length)delayed.shift()();
  await new Promise(r=>setImmediate(r));
  assert.equal(renewed,1);assert.equal(new URL(created.at(-1)).origin,'ws://127.0.0.1:19884');
  lazy.__discordProtonStreams.remover();
  console.log('OK: helper starts on demand; stream reconnect waits for readiness.');
}
lazyStartupTest().catch(error=>{console.error(error);process.exitCode=1;});
