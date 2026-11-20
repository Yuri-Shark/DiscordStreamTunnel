'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),crypto=require('node:crypto');
const source=fs.readFileSync(require('node:path').join(__dirname,'../source/runtime.template.js'),'utf8')
  .replace('/*__CONFIG__*/null',JSON.stringify({relayPort:19884,nonce:'synthetic'}));

function fixture(){
  let now=0,interval;
  const roots={},listeners=new Set();
  const store={getRTCConnections:()=>roots,addChangeListener:f=>listeners.add(f),removeChangeListener:f=>listeners.delete(f)};
  const ctx=vm.createContext({URL,URLSearchParams,crypto,Date:{now:()=>now},setTimeout:()=>1,
    setInterval:f=>{interval=f;return 1;},clearInterval(){},
    Vencord:{Webpack:{findStore:name=>name==='StreamRTCConnectionStore'?store:null}}});ctx.window=ctx;
  class Quality {
    constructor(){this.resolutionWidth=1920;this.resolutionHeight=1080;this.zoom=1;this.incomingVideoEnabled=true;this.requests=[];}
    emit(event,wants){if(event==='requested-streams-update')this.requests.push(wants);}
    updateAudioAndVideoStreamInfo(audio,layers){this.audioSSRC=audio;this.videoStream=layers.find(x=>x.active);this.update();}
    requestStream(){if(this.videoStream)this.emit('requested-streams-update',{
      [this.videoStream.ssrc]:100,pixelCounts:{[this.videoStream.ssrc]:Math.floor(this.resolutionWidth*this.resolutionHeight*this.zoom**2)}});}
    update(){if(this.incomingVideoEnabled)this.requestStream();else if(this.videoStream)this.emit('requested-streams-update',{[this.videoStream.ssrc]:0});}
  }
  function scan(){for(const f of listeners)f();}
  function add(key,{sender=false,context='stream',supported=true,layers=false}={}){
    const row={type:'video',sinkWantLocalAsInt:100,framesDecoded:100,framesEncoded:100,packetsReceived:1000,packetsSent:1000,packetsLost:0};
    const q=supported?new Quality():{};
    const available=[{ssrc:11,quality:100,active:true},...(layers?[{ssrc:12,quality:50,active:true}]:[])];
    if(supported)q.updateAudioAndVideoStreamInfo(1,available);
    const root={context,state:'RTC_CONNECTED',isStreamSender:()=>sender,_goLiveQualityManager:q,
      reconnects:0,reconnect(){this.reconnects++;},
      _socket:{url:'wss://test.discord.media/',webSocket:{url:'ws://127.0.0.1:19884/relay?synthetic'},createWebSocket(){}},
      _connection:{getStats:async()=>({rtp:{inbound:{privateUser:[row]},outbound:[row]}})}};
    roots[key]=root;scan();return {root,q,row,available};
  }
  vm.runInContext(source,ctx);ctx.__discordProtonStreams.automatico(true);
  return {ctx,add,roots,scan,status:()=>ctx.__discordProtonStreams.status().playback.multiScreen,
    async tick(){now+=5000;await interval();},
    remove(){ctx.__discordProtonStreams.remover();assert.equal(listeners.size,0);}};
}
function pixels(q){return Object.values(q.requests.at(-1).pixelCounts||{}).reduce((a,b)=>a+b,0);}

(async()=>{
  {
    const f=fixture(),a=f.add('single');a.q.update();
    assert.equal(pixels(a.q),1920*1080,'a single viewer keeps its native quality');
    const sender=f.add('sender',{sender:true}),voice=f.add('voice',{context:'default'});
    assert.equal(Object.hasOwn(sender.q,'emit'),false);assert.equal(Object.hasOwn(voice.q,'emit'),false);
    const b=f.add('second'),c=f.add('third');
    assert(pixels(a.q)+pixels(b.q)+pixels(c.q)<=1280*720,'three visible tiles share one aggregate pixel budget');
    assert.equal(a.q.resolutionWidth,1920);assert.equal(a.q.resolutionHeight,1080);assert.equal(a.q.zoom,1);
    assert.equal(f.status().activeReceivers,3);assert.equal(f.status().limitedReceivers,3);
    a.q.resolutionWidth=Math.round(1920*1.18);f.scan();
    assert(pixels(a.q)+pixels(b.q)+pixels(c.q)<=1280*720,'a meaningful resize rebalances small decreases together with increases');
    a.q.resolutionWidth=1920;f.scan();
    b.root.state='RTC_CONNECTING';f.scan();
    assert.equal(f.status().activeReceivers,3,'a visible reconnecting screen retains its share');
    assert(pixels(a.q)<=1280*720/3,'neighbors cannot spike to full demand during a reconnect');
    b.root.state='RTC_CONNECTED';f.scan();
    assert.equal(a.root.reconnects+b.root.reconnects+c.root.reconnects,0,'adjusting quality must not reconnect');
    delete f.roots.second;delete f.roots.third;f.scan();
    assert.equal(pixels(a.q),1920*1080,'closing other tiles restores native quality');f.remove();
    assert.equal(Object.hasOwn(a.q,'emit'),false,'removal restores the inherited emitter');
  }
  {
    const f=fixture(),a=f.add('reconnect-a'),b=f.add('reconnect-b');await f.tick();
    for(let i=0;i<2;i++){
      for(const item of [a,b]){item.row.packetsReceived+=80;item.row.packetsLost+=40;item.row.framesDecoded++;}
      await f.tick();
    }
    const cap=pixels(a.q);assert.equal(f.status().level,1);
    // Native reconnect replaces/removes the socket before a new relay URL
    // exists. Merely changing state with the old URL misses this interval.
    b.root.state='RTC_DISCONNECTED';b.root._socket=null;f.scan();
    assert.equal(f.status().activeReceivers,2,'the socket gap must retain the requested screen');
    assert.equal(f.status().level,1,'reconnecting a screen must not erase congestion history');
    assert.equal(pixels(a.q),cap,'a neighboring screen must not jump to full demand');
    a.root.state='RTC_CONNECTING';a.root._socket=null;
    b.root._goLiveQualityManager=new b.q.constructor();
    b.root._goLiveQualityManager.resolutionWidth=0;b.root._goLiveQualityManager.resolutionHeight=0;f.scan();
    assert.equal(f.status().activeReceivers,2,'replacement managers can briefly have no dimensions');
    assert.equal(f.status().level,1);
    b.root._goLiveQualityManager.incomingVideoEnabled=false;f.scan();
    assert.equal(f.status().activeReceivers,1,'an explicit pause releases a reconnect reservation');
    f.remove();
  }
  {
    const f=fixture(),a=f.add('bounded-a'),b=f.add('bounded-b');
    b.root.state='RTC_DISCONNECTED';b.root._socket=null;f.scan();
    for(let i=0;i<5;i++)await f.tick();
    assert.equal(f.status().activeReceivers,2,'a recent reconnect retains its budget');
    await f.tick();assert.equal(f.status().activeReceivers,1,'a dead connection cannot reserve quality indefinitely');
    assert.equal(pixels(a.q),1920*1080);f.remove();
  }
  {
    const f=fixture(),a=f.add('grid-a'),b=f.add('grid-b');
    a.q.resolutionWidth=b.q.resolutionWidth=456;a.q.resolutionHeight=b.q.resolutionHeight=256;f.scan();
    assert(pixels(a.q)<456*256,'small grid tiles are also bounded before congestion');
    a.q.resolutionWidth=1920;a.q.resolutionHeight=1080;f.scan();
    assert(pixels(a.q)>pixels(b.q),'display area gives the larger tile a larger share');
    a.q.resolutionWidth=0;f.scan();a.q.update();
    assert.equal(pixels(a.q),0,'hidden tiles remain hidden');assert.equal(f.status().activeReceivers,1);
    assert.equal(pixels(b.q),456*256,'hidden tiles consume no multi-screen budget');f.remove();
  }
  {
    const f=fixture(),a=f.add('layer-a',{layers:true}),b=f.add('layer-b',{layers:true});
    a.q.updateAudioAndVideoStreamInfo(1,a.available);b.q.updateAudioAndVideoStreamInfo(1,b.available);
    assert.equal(a.q.videoStream.ssrc,12);assert.equal(b.q.videoStream.ssrc,12);
    assert.equal(f.status().lowLayerReceivers,2);
    a.q.updateAudioAndVideoStreamInfo(1,[{ssrc:21,quality:100,active:true},{ssrc:22,quality:50,active:false}]);
    assert.equal(a.q.videoStream.ssrc,21,'inactive or retired layers are never requested');
    delete f.roots['layer-a'];f.scan();assert.equal(b.q.videoStream.ssrc,11,'a lone screen returns to the native layer');f.remove();
  }
  {
    const f=fixture(),a=f.add('loss-a'),b=f.add('loss-b');await f.tick();
    const initial=pixels(a.q);
    for(let i=0;i<2;i++){
      for(const item of [a,b]){item.row.packetsReceived+=80;item.row.packetsLost+=40;item.row.framesDecoded++;}
      await f.tick();
    }
    assert.equal(f.status().level,1,'sustained recent loss lowers requests');assert(pixels(a.q)<initial);
    for(let i=0;i<6;i++){
      for(const item of [a,b]){item.row.packetsReceived+=80;item.row.framesDecoded++;}
      await f.tick();
    }
    assert.equal(f.status().level,0,'six healthy windows slowly restore the base budget');
    // Historical loss is not new loss, and counter resets are not a loss spike.
    a.row.packetsReceived=b.row.packetsReceived=10;a.row.packetsLost=b.row.packetsLost=0;await f.tick();
    assert.equal(f.status().level,0);
    for(let i=0;i<3;i++)await f.tick();assert.equal(f.status().level,0,'static samples cannot create congestion');f.remove();
  }
  {
    const f=fixture(),a=f.add('fresh'),b=f.add('stale');await f.tick();
    for(let i=0;i<2;i++){
      for(const item of [a,b]){item.row.packetsReceived+=80;item.row.packetsLost+=40;item.row.framesDecoded++;}
      await f.tick();
    }
    assert.equal(f.status().level,1);
    b.root._connection.getStats=async()=>{throw Error('stats unavailable');};
    for(let i=0;i<8;i++){a.row.packetsReceived+=80;a.row.framesDecoded++;await f.tick();}
    assert.equal(f.status().level,1,'missing receiver stats cannot prove that all screens recovered');
    b.root._connection.getStats=async()=>({rtp:{inbound:{privateUser:[b.row]}}});
    for(let i=0;i<8;i++){
      for(const item of [a,b])item.row.packetsReceived+=80;
      await f.tick();
    }
    assert.equal(f.status().level,1,'packets without decoded progress cannot establish healthy playback');f.remove();
  }
  {
    const f=fixture(),a=f.add('toggle-a',{layers:true}),b=f.add('toggle-b',{layers:true});
    a.q.updateAudioAndVideoStreamInfo(1,a.available);b.q.updateAudioAndVideoStreamInfo(1,b.available);
    f.ctx.__discordProtonStreams.otimizarTelas(false);
    assert.equal(Object.hasOwn(a.q,'emit'),false);assert.equal(a.q.videoStream.ssrc,11);
    assert.equal(pixels(a.q),1920*1080);assert.equal(f.status().limitedReceivers,0);
    f.ctx.__discordProtonStreams.otimizarTelas(true);assert.equal(f.status().limitedReceivers,2);
    const other=function(){return 'another-plugin';};a.q.emit=other;
    f.remove();assert.equal(a.q.emit,other,'cleanup must preserve a later patch from another plugin');
  }
  {
    const f=fixture(),a=f.add('early-a',{layers:true}),b=f.add('early-b');
    const previous=a.q, manager=new a.q.constructor();
    a.root._goLiveQualityManager=manager;
    a.root._socket={...a.root._socket,createWebSocket(){}};
    // Native connect assigns its socket and can deliver layer metadata before
    // the store emits another change. The setter must attach the quality hook.
    manager.updateAudioAndVideoStreamInfo(1,a.available);
    assert.equal(manager.videoStream.ssrc,12,'early layer metadata on a replacement manager must not be missed');
    assert(pixels(manager)<1920*1080,'a replacement manager retains the existing quality cap');
    assert.equal(Object.hasOwn(previous,'emit'),false);f.remove();
  }
  {
    const f=fixture(),a=f.add('stable-a'),b=f.add('stable-b');
    const updates=a.q.requests.length+b.q.requests.length;
    for(let i=0;i<20;i++){a.q.resolutionWidth=1920+i%2;f.scan();}
    assert.equal(a.q.requests.length+b.q.requests.length,updates,'one-pixel layout changes must not resend quality requests');
    assert.equal(f.status().layerInfoPendingReceivers,2,'unknown layer information is distinguished from no lower layer');
    a.q.updateAudioAndVideoStreamInfo(1,a.available);b.q.updateAudioAndVideoStreamInfo(1,b.available);f.scan();
    assert.equal(f.status().layerInfoPendingReceivers,0);assert.equal(f.status().lowerLayerAvailableReceivers,0);f.remove();
  }
  {
    const f=fixture(),a=f.add('supported'),b=f.add('unsupported',{supported:false});
    assert.equal(f.status().unsupportedReceivers,1);assert(pixels(a.q)<1920*1080);f.remove();
  }
  console.log('OK: aggregate multi-screen budget, recent-loss adaptation, native single-screen restoration, advertised layers, hidden/voice/sender safety and cleanup.');
})().catch(error=>{console.error(error);process.exitCode=1;});
