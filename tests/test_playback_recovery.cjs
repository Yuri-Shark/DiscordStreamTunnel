'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),crypto=require('node:crypto');
const source=fs.readFileSync(require('node:path').join(__dirname,'../source/runtime.template.js'),'utf8')
  .replace('/*__CONFIG__*/null',JSON.stringify({relayPort:19884,nonce:'synthetic'}));
function fixture(){
  let clock=0,interval,cleared=false;
  const events=new Map(),changes=new Set(),timeouts=[],dispatched=[],reports=[];
  const roots={};
  const store={getRTCConnections:()=>roots,addChangeListener:f=>changes.add(f),removeChangeListener:f=>changes.delete(f)};
  const dispatcher={subscribe:(name,f)=>events.set(name,f),unsubscribe:(name,f)=>{assert.equal(events.get(name),f);events.delete(name);},dispatch:event=>dispatched.push(event)};
  const ctx=vm.createContext({URL,URLSearchParams,crypto,Date:{now:()=>clock},
    setTimeout:f=>{timeouts.push(f);return 1;},setInterval:f=>{interval=f;return 42;},clearInterval:id=>{assert.equal(id,42);cleared=true;},
    DiscordProtonStreamsNative:{reportPlayback:value=>reports.push(value)},
    Vencord:{Webpack:{findStore:name=>name==='StreamRTCConnectionStore'?store:null,Common:{FluxDispatcher:dispatcher}}}});ctx.window=ctx;
  vm.runInContext(source,ctx);ctx.__discordProtonStreams.automatico(true);
  function add(key,sender=false){
    const row={type:'video',sinkWantLocalAsInt:100,framesDecoded:0,framesEncoded:0,packetsReceived:0,packetsSent:0,packetsLost:0};
    const root={context:'stream',state:'RTC_CONNECTED',isStreamSender:()=>sender,reconnects:0,reconnect(){this.reconnects++;},
      _goLiveQualityManager:{userId:key,resolutionWidth:1280,resolutionHeight:720,incomingVideoEnabled:true},_socket:{url:'wss://test.discord.media:8443/',webSocket:{url:'ws://127.0.0.1:19884/relay?synthetic'},createWebSocket(){}},
      _connection:{getStats:async()=>({rtp:{inbound:{privateUser:[row]},outbound:[row]}})}};
    roots[key]=root;return {root,row};
  }
  return {ctx,add,roots,events,reports,dispatched,async tick(ms=5000){clock+=ms;await interval();},
    timeout(event){events.get('VIDEO_STREAM_READY_TIMEOUT')?.(event);while(timeouts.length)timeouts.shift()();},
    remove(){ctx.__discordProtonStreams.remover();assert(cleared);assert.equal(events.size,0);assert.equal(changes.size,0);}};
}
(async()=>{
  {
    const f=fixture(),{root,row}=f.add('receiver');
    await f.tick();
    for(let i=0;i<6;i++){row.packetsReceived+=50;await f.tick();}
    assert.equal(root.reconnects,1,'continuous incoming video without decoded progress must recover');
    assert.equal(f.ctx.__discordProtonStreams.status().playback.lastReason,'video-decode-stalled');
    for(let i=0;i<18;i++){row.packetsReceived+=50;await f.tick();}
    assert.equal(root.reconnects,2,'recovery is capped at two attempts per five minutes');
    assert.equal(f.dispatched.length,0,'requesting recovery must not hide the native error');
    const raw=JSON.stringify(f.reports);assert(!raw.includes('privateUser'));assert(!raw.includes('synthetic'));
    f.remove();
  }
  for(const kind of ['static','unrequested','hidden','disabled','inactive-layer','low-fps','missing-counters','sender','unavailable']){
    const f=fixture(),{root,row}=f.add('receiver',kind==='sender');
    if(kind==='unrequested')row.sinkWantLocalAsInt=0;
    if(kind==='hidden')root._goLiveQualityManager.resolutionWidth=0;
    if(kind==='disabled')root._goLiveQualityManager.incomingVideoEnabled=false;
    if(kind==='inactive-layer')root._goLiveQualityManager.videoStream={active:false};
    if(kind==='missing-counters')delete row.framesDecoded;
    if(kind==='unavailable')root._connection.getStats=async()=>{throw Error('Unavailable');};
    await f.tick();
    for(let i=0;i<15;i++){
      if(kind!=='static')row.packetsReceived+=20;
      if(kind==='low-fps')row.framesDecoded++;
      await f.tick();
    }
    assert.equal(root.reconnects,0,kind+' must not trigger a false recovery');f.remove();
  }
  {
    const f=fixture(),{root,row}=f.add('hidden');root._goLiveQualityManager.resolutionHeight=0;
    f.timeout({mediaContext:'stream',streamKey:'hidden'});
    assert.equal(root.reconnects,0,'a hidden tile timeout must not reconnect the stream');
    await f.tick();assert.equal(f.ctx.__discordProtonStreams.status().playback.streams[0].requested,false);
    root._goLiveQualityManager.resolutionHeight=720;await f.tick();
    for(let i=0;i<6;i++){row.packetsReceived+=50;await f.tick();}
    assert.equal(root.reconnects,1,'monitoring resumes when the tile requests pixels again');f.remove();
  }
  {
    const f=fixture(),{root,row}=f.add('older-client');
    delete root._goLiveQualityManager.resolutionWidth;
    delete root._goLiveQualityManager.resolutionHeight;
    await f.tick();
    for(let i=0;i<6;i++){row.packetsReceived+=50;await f.tick();}
    assert.equal(root.reconnects,1,'clients without viewport counters retain sinkWant-based recovery');f.remove();
  }
  {
    const f=fixture(),a=f.add('a'),b=f.add('b'),sender=f.add('sender',true);
    for(const event of [{mediaContext:'default',streamKey:'a'},{mediaContext:'stream',streamKey:'missing'},
      {mediaContext:'stream',streamKey:'sender'}])f.timeout(event);
    assert.equal(a.root.reconnects+b.root.reconnects+sender.root.reconnects,0);
    f.timeout({mediaContext:'stream',streamKey:'a'});
    f.timeout({mediaContext:'stream',streamKey:'b'});
    assert.equal(a.root.reconnects,1);assert.equal(b.root.reconnects,0,'recoveries are staggered');
    await f.tick();assert.equal(b.root.reconnects,1,'a simultaneous timeout must be retried after the global cooldown');
    f.timeout({mediaContext:'stream',streamKey:'a'});assert.equal(a.root.reconnects,1);
    a.row.framesDecoded++;await f.tick();
    assert.equal(f.dispatched.length,0);assert.equal(sender.root.reconnects,0);f.remove();
  }
  {
    const f=fixture(),{root,row}=f.add('removed');let resolve;
    root._connection.getStats=()=>new Promise(r=>{resolve=r;});
    const pending=f.tick();delete f.roots.removed;
    resolve({rtp:{inbound:{privateUser:[row]}}});await pending;
    assert.equal(root.reconnects,0,'late stats cannot revive a closed stream');f.remove();
  }
  console.log('OK: decoded-frame recovery, first-frame timeouts, paused/unrequested/low-FPS safety, retry limits, staggered screens and cleanup.');
})().catch(error=>{console.error(error);process.exitCode=1;});
