'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),crypto=require('node:crypto');
const source=fs.readFileSync(require('node:path').join(__dirname,'../source/runtime.template.js'),'utf8').replace('/*__CONFIG__*/null',JSON.stringify({relayPort:19884,nonce:'synthetic'}));
async function main(){
 const calls=[],listeners=new Set();let helperReady,starts=0,migrations=0,ready=false;
 class NativeSocket{constructor(url){this.url=url;this.readyState=1;calls.push(url);}close(code){assert.equal(code,4000);migrations++;control.webSocket=new ctx.WebSocket('wss://gateway-us-east1-b.discord.gg/?v=9&encoding=etf&compress=zstd-stream');}}
 NativeSocket.OPEN=1;
 const control={webSocket:new NativeSocket('wss://gateway.discord.gg/?v=9&encoding=etf'),isSessionEstablished:()=>true};
 const stream={constructor:{displayName:'StreamRTCConnectionStore'},getRTCConnections:()=>({}),addChangeListener:f=>listeners.add(f),removeChangeListener:f=>listeners.delete(f)};
 const gatewayStore={constructor:{displayName:'GatewayConnectionStore'},getSocket:()=>control};
 const ctx=vm.createContext({WebSocket:NativeSocket,URL,URLSearchParams,crypto,setTimeout,clearTimeout,
  DiscordProtonStreamsNative:{ensureHelper(){starts++;return ready?Promise.resolve():new Promise(resolve=>{helperReady=()=>{ready=true;resolve();};});}},
  Vencord:{Webpack:{cache:{stream:{exports:{stream}},gateway:{exports:{gatewayStore}}},findStore(){throw TypeError('broken Flux lookup');}}}});ctx.window=ctx;
 vm.runInContext(source,ctx);ctx.__discordProtonStreams.automatico(true);
 await new Promise(resolve=>setImmediate(resolve));
 assert.equal(migrations,0,'the adopted fast-connect socket must wait for the helper');assert.equal(ctx.WebSocket,NativeSocket);
 helperReady();await new Promise(resolve=>setImmediate(resolve));
 assert.equal(migrations,1);assert.equal(ctx.__discordProtonStreams.status().gateway.throughRelay,true);
 assert.equal(ctx.WebSocket.OPEN,1);assert(control.webSocket instanceof NativeSocket);
 ctx.__discordProtonStreams.automatico(true);await new Promise(resolve=>setImmediate(resolve));assert.equal(migrations,1,'an already routed gateway must not reconnect again');
 for(const target of ['wss://gateway.discord.gg/?v=9&encoding=json','wss://gateway-us-east1-b.discord.gg/?v=10&encoding=etf&compress=zlib-stream']){
  const socket=new ctx.WebSocket(target);const local=new URL(socket.url);assert.equal(local.pathname,'/gateway');assert.equal(local.searchParams.get('target'),target);assert.equal(local.searchParams.get('nonce'),'synthetic');
 }
 for(const target of ['wss://voice.discord.media/?v=9','wss://gateway.discord.gg/?v=9&encoding=etf&token=unsafe','wss://gateway.discord.gg/?v=9&v=10&encoding=etf','wss://gateway.discord.gg.evil.example/?v=9&encoding=etf','wss://gateway.discord.gg:8443/?v=9&encoding=etf','wss://gateway.discord.gg/not-allowed?v=9&encoding=etf']){
  assert.equal(new ctx.WebSocket(target).url,target,'voice and unexpected targets must remain untouched');
 }
 ctx.__discordProtonStreams.remover();assert.equal(ctx.WebSocket,NativeSocket);assert.equal(listeners.size,0);assert(starts>=2);
 console.log('OK: Gateway waits for helper, migrates fast-connect once, routes only validated Gateway URLs, preserves voice and restores constructor.');
}
async function coldStart(){
 const timers=[],listeners=new Set();let migrations=0,ctx;
 const control={webSocket:null,isSessionEstablished:()=>true};
 class Socket{constructor(url){this.url=url;this.readyState=1;}close(code){assert.equal(code,4000);migrations++;control.webSocket=new ctx.WebSocket('wss://gateway.discord.gg/?v=9&encoding=etf');}}
 const stream={constructor:{displayName:'StreamRTCConnectionStore'},getRTCConnections:()=>({}),addChangeListener:f=>listeners.add(f),removeChangeListener:f=>listeners.delete(f)};
 const gatewayStore={constructor:{displayName:'GatewayConnectionStore'},getSocket:()=>control};
 ctx=vm.createContext({WebSocket:Socket,URL,URLSearchParams,crypto,
  setTimeout(callback){timers.push(callback);return{unref(){}};},clearTimeout(){},
  DiscordProtonStreamsNative:{ensureHelper:()=>Promise.resolve()},
  Vencord:{Webpack:{cache:{stream:{exports:{stream}},gateway:{exports:{gatewayStore}}},findStore(){throw TypeError('broken Flux lookup');}}}});ctx.window=ctx;
 vm.runInContext(source,ctx);ctx.__discordProtonStreams.automatico(true);
 await new Promise(resolve=>setImmediate(resolve));assert.equal(migrations,0);
 const retry=timers.find(callback=>callback.name==='startGateway');assert(retry,'startup must retry when fast-connect has not exposed its socket');
 control.webSocket=new Socket('wss://gateway.discord.gg/?v=9&encoding=etf');retry();
 await new Promise(resolve=>setImmediate(resolve));assert.equal(migrations,1);assert(ctx.__discordProtonStreams.status().gateway.throughRelay);
 ctx.__discordProtonStreams.remover();assert.equal(ctx.WebSocket,Socket);assert.equal(listeners.size,0);
 console.log('OK: cold startup retries a missing fast-connect socket and migrates it after it becomes available.');
}
main().then(coldStart).catch(error=>{console.error(error);process.exitCode=1;});
