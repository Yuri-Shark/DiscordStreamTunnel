'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const source=path.resolve(__dirname,'../source'),version=JSON.parse(fs.readFileSync(path.join(source,'version.json'))).version;
function fixture(initial){
 let bridge,clock=0,spawns=0,phase=initial,healthRequests=0;
 const files={
  'local-settings.json':JSON.stringify({relayPort:12345,nonce:'synthetic'}),
  'installation.json':JSON.stringify({worker:'synthetic-worker'}),
  'version.json':JSON.stringify({version}),
  'runtime.template.js':fs.readFileSync(path.join(source,'runtime.template.js'),'utf8')
 };
 const virtualFS={existsSync:()=>true,readFileSync:file=>{const value=files[path.basename(file)];if(value===undefined)throw Error('UnexpectedRead');return value;}};
 const http={get(options,callback){
  const events={};const request={once(event,fn){events[event]=fn;return this;},destroy(){}};
  const warm=options.path.startsWith('/warmup');
  const current=phase;
  if(!warm){healthRequests++;if(phase==='old'||phase==='legacy'||phase==='wrong-version')phase='offline';}
  queueMicrotask(()=>{
   if(current==='offline'||current==='unavailable'){events.error?.(Error('NotListening'));return;}
   const health={service:'DiscordStreamTunnel',protocolVersion:1,configurationReady:true,version:current==='wrong-version'?'0.0.0':version,ownerPID:current==='old'?1111:current==='legacy'?undefined:4321};
   callback({statusCode:200,on(event,fn){if(event==='data')fn(JSON.stringify(health));if(event==='end')fn();return this;}});
  });return request;
 }};
 const modules={fs:virtualFS,path,crypto:require('crypto'),http,
  child_process:{spawn(){spawns++;if(phase!=='unavailable')phase='current';return {on(){},unref(){}};}},
  'electron/renderer':{ipcRenderer:{invoke:()=>Promise.resolve()},webFrame:{executeJavaScript:()=>Promise.resolve()},contextBridge:{exposeInMainWorld(_name,value){bridge=value;}}},
  './repair.cjs':{ensure(){}},'./update.cjs':{check:()=>Promise.resolve()},'./update-ui.cjs':{start:()=>Promise.resolve()}};
 const context=vm.createContext({require:name=>modules[name],__dirname:source,module:{exports:{}},location:{protocol:'https:',hostname:'discord.com'},process:{ppid:4321,platform:'win32',env:{}},URL,
  Date:{now:()=>clock},setTimeout(fn,ms){clock+=ms;queueMicrotask(fn);}});
 vm.runInContext(fs.readFileSync(path.join(source,'native.cjs'),'utf8'),context);context.module.exports.start('synthetic-root');
 return {ensure:()=>bridge.ensureHelper(),lose(){phase='offline';},counts:()=>({spawns,healthRequests})};
}
(async()=>{
 for(const state of ['old','legacy','wrong-version']){
  const test=fixture(state);await test.ensure();
  assert.equal(test.counts().spawns,1,state+' helper must not be accepted');
 }
 const live=fixture('current');await live.ensure();assert.equal(live.counts().spawns,0);
 live.lose();await live.ensure();assert.equal(live.counts().spawns,1,'helper is relaunched after it disappears');
 const failed=fixture('unavailable');await assert.rejects(failed.ensure(),/HelperUnavailable/);
 assert.equal(failed.counts().spawns,1,'failed startup must not spawn an unbounded number of workers');
 console.log('OK: previous-owner/legacy/wrong-version helpers rejected, lost helper relaunched, startup bounded.');
})().catch(error=>{console.error(error);process.exitCode=1;});
