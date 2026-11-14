'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),crypto=require('node:crypto');
const source=fs.readFileSync(require('node:path').join(__dirname,'../source/runtime.template.js'),'utf8')
  .replace('/*__CONFIG__*/null',JSON.stringify({relayPort:19884,nonce:'synthetic'}));
function fixture({nativeTimeoutStore=false}={}){
  let clock=0,interval,cleared=false;
  const events=new Map(),changes=new Set(),timeouts=[],dispatched=[],reports=[];
  const roots={},nativeTimeouts={};
  const videoStore={getTimedoutVideos:()=>nativeTimeouts};
  const store={getRTCConnections:()=>roots,addChangeListener:f=>changes.add(f),removeChangeListener:f=>changes.delete(f)};
  const dispatcher={subscribe:(name,f)=>events.set(name,f),unsubscribe:(name,f)=>{assert.equal(events.get(name),f);events.delete(name);},dispatch:event=>dispatched.push(event)};
  const ctx=vm.createContext({URL,URLSearchParams,crypto,Date:{now:()=>clock},
    setTimeout:f=>{timeouts.push(f);return 1;},setInterval:f=>{interval=f;return 42;},clearInterval:id=>{assert.equal(id,42);cleared=true;},
    DiscordProtonStreamsNative:{reportPlayback:value=>reports.push(value)},
    Vencord:{Webpack:{findStore:name=>name==='StreamRTCConnectionStore'?store
      :name==='VideoStreamStore'&&nativeTimeoutStore?videoStore:null,Common:{FluxDispatcher:dispatcher}}}});ctx.window=ctx;
  vm.runInContext(source,ctx);ctx.__discordProtonStreams.automatico(true);
  function add(key,sender=false){
    const row={type:'video',sinkWantLocalAsInt:100,framesDecoded:0,framesEncoded:0,packetsReceived:0,packetsSent:0,packetsLost:0};
    const root={context:'stream',state:'RTC_CONNECTED',isStreamSender:()=>sender,reconnects:0,reconnect(){this.reconnects++;},
      _goLiveQualityManager:{userId:key,resolutionWidth:1280,resolutionHeight:720,incomingVideoEnabled:true},_socket:{url:'wss://test.discord.media:8443/',webSocket:{url:'ws://127.0.0.1:19884/relay?synthetic'},createWebSocket(){}},
      _connection:{getStats:async()=>({rtp:{inbound:{privateUser:[row]},outbound:[row]}})}};
    roots[key]=root;return {root,row};
  }
  return {ctx,add,roots,nativeTimeouts,events,reports,dispatched,async tick(ms=5000){clock+=ms;await interval();},
    signal(event){events.get(event.type)?.(event);},
    flush(){while(timeouts.length)timeouts.shift()();},
    timeout(event){events.get('VIDEO_STREAM_READY_TIMEOUT')?.(event);while(timeouts.length)timeouts.shift()();},
    remove(){ctx.__discordProtonStreams.remover();assert(cleared);assert.equal(events.size,0);assert.equal(changes.size,0);}};
}
(async()=>{
  {
    const f=fixture(),{root,row}=f.add('hidden-diagnostics');
    row.framesDecoded=150;row.packetsReceived=1000;row.packetsLost=37;await f.tick();
    root._goLiveQualityManager.resolutionWidth=0;await f.tick();
    const sample=f.ctx.__discordProtonStreams.status().playback.streams[0];
    assert.equal(sample.requested,false);
    assert.equal(sample.frames,150,'hiding a tile must not erase its native diagnostic counters');
    assert.equal(sample.packets,1000);assert.equal(sample.packetsLost,37);
    assert.equal(root.reconnects,0);f.remove();
  }
  {
    const f=fixture({nativeTimeoutStore:true}),{root,row}=f.add('already-2012');
    row.framesDecoded=100;
    f.nativeTimeouts.receiver={mediaContext:'stream',streamKey:'already-2012',userId:'already-2012',videoStreamId:'private-video'};
    await f.tick();
    assert.equal(root.reconnects,1,'a viewer timeout already stored before subscription must reconnect without a new Flux event');
    const recovered={...row,framesDecoded:1};
    root._connection={getStats:async()=>({rtp:{inbound:{privateUser:[recovered]}}})};
    await f.tick();await f.tick(30000);
    assert.equal(root.reconnects,1,'a stale stored error must not restart video whose recovery was confirmed');
    delete f.nativeTimeouts.receiver;await f.tick();
    f.nativeTimeouts.receiver={mediaContext:'stream',userId:'already-2012',videoStreamId:'next-video'};
    await f.tick();assert.equal(root.reconnects,2,'a later stored timeout must be detected again');
    const raw=JSON.stringify(f.reports);assert(!raw.includes('private-video'));assert(!raw.includes('already-2012'));f.remove();
  }
  {
    const f=fixture({nativeTimeoutStore:true}),{root}=f.add('hidden-error'),sender=f.add('sender-error',true);
    f.nativeTimeouts.hidden={mediaContext:'stream',streamKey:'hidden-error'};
    f.nativeTimeouts.sender={mediaContext:'stream',streamKey:'sender-error'};
    root._goLiveQualityManager.resolutionWidth=0;
    await f.tick();assert.equal(root.reconnects+sender.root.reconnects,0);
    root._goLiveQualityManager.resolutionWidth=1280;await f.tick();
    assert.equal(root.reconnects,1,'a stored timeout on a hidden tile is recovered when it becomes visible');
    assert.equal(sender.root.reconnects,0);f.remove();
  }
  {
    const f=fixture(),{root,row}=f.add('later-timeout');
    row.framesDecoded=100;row.packetsReceived=500;await f.tick(3600000);
    f.timeout({mediaContext:'stream',streamKey:'later-timeout'});
    assert.equal(root.reconnects,1);
    await f.tick(30000);
    assert.equal(root.reconnects,2,'an unresolved timeout after normal viewing must retry without another native event or incoming video');
    assert.equal(f.ctx.__discordProtonStreams.status().playback.streams[0].timeoutPending,true);
    for(let i=0;i<20;i++)await f.tick();
    assert.equal(root.reconnects,2,'a pending native timeout retains the recovery limit');
    assert.equal(f.dispatched.length,0,'recovery must not clear the error before video returns');f.remove();
  }
  for(const kind of ['missing','rejecting','hanging']){
    const f=fixture(),{root}=f.add('stats-failure');let release,pending;
    if(kind==='missing')delete root._connection.getStats;
    if(kind==='rejecting')root._connection.getStats=async()=>{throw Error('Unavailable');};
    if(kind==='hanging'){
      root._connection.getStats=()=>new Promise(resolve=>{release=resolve;});pending=f.tick();
    }
    f.timeout({mediaContext:'stream',streamKey:'stats-failure'});
    await f.tick(30000);
    assert.equal(root.reconnects,2,kind+' stats must not block recovery from an explicit native timeout');
    f.remove();if(release){release({});await pending;}
  }
  {
    const f=fixture(),{root,row}=f.add('recovered');row.framesDecoded=100;await f.tick();
    f.timeout({mediaContext:'stream',streamKey:'recovered'});
    const next={...row,framesDecoded:1};
    root._connection={getStats:async()=>({rtp:{inbound:{privateUser:[next]}}})};
    await f.tick();await f.tick(30000);
    assert.equal(root.reconnects,1,'decoded video on a replacement connection resolves the timeout even when its frame counter resets');
    assert.equal(f.ctx.__discordProtonStreams.status().playback.streams[0].timeoutPending,false);
    f.remove();
  }
  {
    const f=fixture(),{root,row}=f.add('same-connection');row.framesDecoded=100;await f.tick();
    f.timeout({mediaContext:'stream',streamKey:'same-connection'});
    await f.tick();row.framesDecoded++;await f.tick();await f.tick(30000);
    assert.equal(root.reconnects,1,'decoded progress on the same connection also resolves a native timeout');f.remove();
  }
  {
    const f=fixture(),a=f.add('visible'),b=f.add('hidden');
    f.timeout({mediaContext:'stream',streamKey:'visible'});
    f.signal({type:'CLEAR_VIDEO_STREAM_READY_TIMEOUT',mediaContext:'default',userId:'visible'});
    f.signal({type:'CLEAR_VIDEO_STREAM_READY_TIMEOUT',mediaContext:'stream',userId:'hidden'});
    await f.tick(30000);assert.equal(a.root.reconnects,2,'unrelated ready events cannot resolve another stream timeout');
    await f.tick();
    f.timeout({mediaContext:'stream',streamKey:'hidden'});
    b.root._goLiveQualityManager.resolutionWidth=0;
    await f.tick(30000);assert.equal(b.root.reconnects,1,'pending timeout recovery respects a tile hidden after the error');f.remove();
  }
  {
    const f=fixture(),{root}=f.add('native-ready');
    f.timeout({mediaContext:'stream',streamKey:'native-ready'});
    f.signal({type:'CLEAR_VIDEO_STREAM_READY_TIMEOUT',mediaContext:'stream',userId:'native-ready'});
    await f.tick(30000);assert.equal(root.reconnects,1,'native first-frame confirmation stops timeout retries');
    f.signal({type:'VIDEO_STREAM_READY_TIMEOUT',mediaContext:'stream',streamKey:'native-ready'});
    f.signal({type:'CLEAR_VIDEO_STREAM_READY_TIMEOUT',mediaContext:'stream',userId:'native-ready'});
    f.flush();assert.equal(root.reconnects,1,'a cleared timeout queued by Flux cannot reconnect recovered video');
    f.signal({type:'VIDEO_STREAM_READY_TIMEOUT',mediaContext:'stream',streamKey:'native-ready'});
    root._connection={getStats:async()=>({})};f.flush();
    assert.equal(root.reconnects,1,'a queued timeout from a replaced connection cannot reconnect its successor');f.remove();
  }
  {
    const f=fixture(),{root,row}=f.add('old-stats');let release;
    root._connection.getStats=()=>new Promise(resolve=>{release=resolve;});const old=f.tick();
    root._connection={getStats:async()=>({rtp:{inbound:{privateUser:[row]}}})};
    for(let i=0;i<7;i++){row.packetsReceived+=20;await f.tick();}
    assert.equal(root.reconnects,1,'hung stats on an old connection cannot disable monitoring of its successor');
    f.remove();release({});await old;
  }
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
  console.log('OK: persistent viewer-timeout recovery, failed/hung stats, confirmed video recovery, stale events, paused/unrequested/low-FPS safety, retry limits, staggered screens and cleanup.');
})().catch(error=>{console.error(error);process.exitCode=1;});
