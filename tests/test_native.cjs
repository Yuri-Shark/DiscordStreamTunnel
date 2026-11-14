const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),os=require('node:os');
const source=path.join(__dirname,'..','source'),repair=require('../source/repair.cjs');
const temp=fs.mkdtempSync(path.join(os.tmpdir(),'dts-native-test-'));
async function main(){try{
 fs.mkdirSync(path.join(temp,'data'));const preload=path.join(temp,'preload.js');fs.writeFileSync(preload,'// Vencord initial\n');
 fs.writeFileSync(path.join(temp,'installation.json'),JSON.stringify({preload,worker:'must-not-spawn'}));
 fs.writeFileSync(path.join(temp,'active.json'),JSON.stringify({version:'1.1.0'}));
 fs.writeFileSync(path.join(temp,'data','private-core.json'),'{}');fs.writeFileSync(path.join(temp,'data','local-settings.json'),JSON.stringify({relayPort:12345,nonce:'synthetic'}));
 let bridge,updateChecks=0,originalInvoked=0;const scripts=[];
 const ipc={invoke(channel){originalInvoked++;if(channel==='VencordBuild')fs.writeFileSync(preload,'// Vencord updated upstream\n');return Promise.resolve({ok:true,value:true});}};
 const http={get(_options,callback){const request={once(){return this;},destroy(){}};queueMicrotask(()=>callback({statusCode:200,on(event,fn){if(event==='data')fn(JSON.stringify({service:'DiscordStreamTunnel',protocolVersion:1,configurationReady:true,version:JSON.parse(fs.readFileSync(path.join(source,'version.json'))).version,ownerPID:4321}));if(event==='end')fn();return this;}}));return request;}};
 const modules={fs:{...fs,promises:fs.promises},path,crypto:require('crypto'),child_process:{spawn(){throw Error('Unexpected spawn');}},http,'electron/renderer':{ipcRenderer:ipc,webFrame:{executeJavaScript:script=>{scripts.push(script);return Promise.resolve();}},contextBridge:{exposeInMainWorld(_name,value){bridge=value;}}},'./repair.cjs':repair,'./update.cjs':{check(){updateChecks++;return Promise.resolve();}},'./update-ui.cjs':{start(){return Promise.resolve();}}};
 const context=vm.createContext({require:name=>modules[name],__dirname:source,module:{exports:{}},location:{protocol:'https:',hostname:'discord.com'},process:{ppid:4321,platform:'win32',env:{}},setTimeout,URL});
 vm.runInContext(fs.readFileSync(path.join(source,'native.cjs'),'utf8'),context);context.module.exports.start(temp);
 assert.equal(typeof bridge.ensureHelper,'function');await bridge.ensureHelper();
 let releaseWrite,writes=0;const writeGate=new Promise(resolve=>{releaseWrite=resolve;});
 const nativeFs=modules.fs,originalWrite=fs.promises.writeFile;
 // Simulate a disk stalled by scanning/flush without blocking the renderer.
 // Reports arriving meanwhile must not race over the same temporary file.
 Object.defineProperty(nativeFs,'promises',{configurable:true,value:{...fs.promises,async writeFile(...args){writes++;await writeGate;return originalWrite(...args);}}});
 const reporting=bridge.reportPlayback({attempts:1,lastReason:'video-decode-stalled',token:'DO-NOT-SHARE',streams:[{sender:false,requested:true,timeoutPending:true,frames:42,packets:100,decodeErrors:NaN,state:'RTC_CONNECTED',userId:'DO-NOT-SHARE'}]});
 try{
  assert.equal(writes,1);assert.equal(fs.existsSync(path.join(temp,'data','playback-status.json')),false);
  await bridge.reportPlayback({attempts:99,streams:[]});assert.equal(writes,1,'slow reports must not queue more disk writes');
 }finally{releaseWrite();await reporting;Object.defineProperty(nativeFs,'promises',{configurable:true,value:{...nativeFs.promises,writeFile:originalWrite}});}
 const playback=fs.readFileSync(path.join(temp,'data','playback-status.json'),'utf8');
 assert(!playback.includes('DO-NOT-SHARE'));assert(!playback.includes('decodeErrors'));assert.equal(JSON.parse(playback).streams[0].frames,42);
 assert.equal(JSON.parse(playback).streams[0].timeoutPending,true);
 const store={constructor:{displayName:'StreamRTCConnectionStore'},getRTCConnections:()=>({}),addChangeListener(){},removeChangeListener(){}};
 const renderer=vm.createContext({URL,URLSearchParams,crypto:require('crypto'),setTimeout,Vencord:{Webpack:{findStore(){throw TypeError('Reflect.get called on non-object');},cache:{stream:{exports:{store}}}}}});renderer.window=renderer;
 await vm.runInContext(scripts[0],renderer);
 assert.equal(renderer.__discordProtonStreams.status().automatic,true,'native bootstrap must allow runtime store fallback');
 let polls=0;const late=vm.createContext({URL,URLSearchParams,crypto:require('crypto'),setTimeout(fn){polls++;if(polls===170)late.Vencord=renderer.Vencord;queueMicrotask(fn);}});late.window=late;
 await vm.runInContext(scripts[0],late);
 assert.equal(late.__discordProtonStreams.status().automatic,true,'a Discord update taking longer than the old startup window must still initialize');
 // Vencord exposes Webpack before Discord has loaded its Flux modules. Calling
 // findStore then exhausts Flux's lazy lookup and breaks unrelated UI plugins.
 let resolveReady,discordReady=false,earlyLookups=0,earlyScans=0;
 const onceReady=new Promise(resolve=>{resolveReady=resolve;});
 const cold=vm.createContext({URL,URLSearchParams,crypto:require('crypto'),setTimeout(){},Vencord:{Webpack:{onceReady,
  findStore(){if(!discordReady){earlyLookups++;throw Error('Flux is not ready');}return null;},
  get cache(){earlyScans++;return undefined;}}}});cold.window=cold;
 const coldBoot=vm.runInContext(scripts[0],cold);
 await Promise.resolve();
 assert.equal(earlyLookups,0,'cold startup must not consume Vencord lazy lookup attempts before onceReady');
 assert.equal(earlyScans,0,'cold startup must wait before scanning incomplete module exports');
 assert.equal(cold.__discordProtonStreams,undefined);
 Object.defineProperty(cold.Vencord.Webpack,'cache',{value:{stream:{exports:{store}}}});
 discordReady=true;resolveReady();await coldBoot;
 assert.equal(cold.__discordProtonStreams.status().automatic,true,'the tunnel must activate after Discord is ready');
 assert.equal(earlyLookups,0,'startup must not look up Flux before Discord is ready');
 // CONNECTION_OPEN may be blocked on the direct route. Module availability is
 // sufficient; waiting for an authenticated Gateway here would deadlock startup.
 let fluxLoaded;const neverConnected=new Promise(()=>{});
 const offline=vm.createContext({URL,URLSearchParams,crypto:require('crypto'),setTimeout,
  Vencord:{Webpack:{onceReady:neverConnected,waitFor(filter,callback){assert.equal(filter[0],'connectStores');fluxLoaded=callback;},
   findStore(){throw Error('must use loaded store');}}}});offline.window=offline;
 const offlineBoot=vm.runInContext(scripts[0],offline);
 assert.equal(typeof fluxLoaded,'function');assert.equal(offline.__discordProtonStreams,undefined);
 offline.Vencord.Webpack.cache={stream:{exports:{store}}};fluxLoaded();
 let timeout;try{await Promise.race([offlineBoot,new Promise((_,reject)=>{timeout=setTimeout(()=>reject(Error('bootstrap waited for Gateway instead of Flux')),1000);})]);}finally{clearTimeout(timeout);}
 assert.equal(offline.__discordProtonStreams.status().automatic,true,'Gateway routing must start before CONNECTION_OPEN');
 const result=await ipc.invoke('VencordBuild');assert.equal(result.ok,true);assert.equal(originalInvoked,1);
 assert.ok(fs.readFileSync(preload,'utf8').includes(repair.START));assert.equal(repair.clean(fs.readFileSync(preload,'utf8')),'// Vencord updated upstream\n');
 assert.equal(updateChecks,1);
 console.log('OK: native bootstrap repairs Vencord updates, tolerates late webpack initialization and checks signed updates.');
}finally{if(path.dirname(temp)!==os.tmpdir()||!path.basename(temp).startsWith('dts-native-test-'))throw Error('Unsafe cleanup');fs.rmSync(temp,{recursive:true,force:true});}}
main().catch(error=>{console.error(error);process.exitCode=1;});
