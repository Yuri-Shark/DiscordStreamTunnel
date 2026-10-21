const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),os=require('node:os');
const source=path.join(__dirname,'..','source'),repair=require('../source/repair.cjs');
const temp=fs.mkdtempSync(path.join(os.tmpdir(),'dts-native-test-'));
async function main(){try{
 fs.mkdirSync(path.join(temp,'data'));const preload=path.join(temp,'preload.js');fs.writeFileSync(preload,'// Vencord initial\n');
 fs.writeFileSync(path.join(temp,'installation.json'),JSON.stringify({preload,worker:'must-not-spawn'}));
 fs.writeFileSync(path.join(temp,'active.json'),JSON.stringify({version:'1.1.0'}));
 fs.writeFileSync(path.join(temp,'data','private-core.json'),'{}');fs.writeFileSync(path.join(temp,'data','local-settings.json'),JSON.stringify({relayPort:12345,nonce:'synthetic'}));
 let bridge,updateChecks=0,originalInvoked=0;
 const ipc={invoke(channel){originalInvoked++;if(channel==='VencordBuild')fs.writeFileSync(preload,'// Vencord updated upstream\n');return Promise.resolve({ok:true,value:true});}};
 const http={get(_options,callback){const request={once(){return this;},destroy(){}};queueMicrotask(()=>callback({statusCode:200,on(event,fn){if(event==='data')fn(JSON.stringify({service:'DiscordStreamTunnel',protocolVersion:1,configurationReady:true}));if(event==='end')fn();return this;}}));return request;}};
 const modules={fs,path,crypto:require('crypto'),child_process:{spawn(){throw Error('Unexpected spawn');}},http,'electron/renderer':{ipcRenderer:ipc,webFrame:{executeJavaScript:()=>Promise.resolve()},contextBridge:{exposeInMainWorld(_name,value){bridge=value;}}},'./repair.cjs':repair,'./update.cjs':{check(){updateChecks++;return Promise.resolve();}}};
 const context=vm.createContext({require:name=>modules[name],__dirname:source,module:{exports:{}},location:{protocol:'https:',hostname:'discord.com'},process:{ppid:4321,platform:'win32',env:{}},setTimeout,URL});
 vm.runInContext(fs.readFileSync(path.join(source,'native.cjs'),'utf8'),context);context.module.exports.start(temp);
 assert.equal(typeof bridge.ensureHelper,'function');await bridge.ensureHelper();
 const result=await ipc.invoke('VencordBuild');assert.equal(result.ok,true);assert.equal(originalInvoked,1);
 assert.ok(fs.readFileSync(preload,'utf8').includes(repair.START));assert.equal(repair.clean(fs.readFileSync(preload,'utf8')),'// Vencord updated upstream\n');
 assert.equal(updateChecks,1);
 console.log('OK: actual native bootstrap repairs preload before VencordBuild promise resolves and starts background update check.');
}finally{if(path.dirname(temp)!==os.tmpdir()||!path.basename(temp).startsWith('dts-native-test-'))throw Error('Unsafe cleanup');fs.rmSync(temp,{recursive:true,force:true});}}
main().catch(error=>{console.error(error);process.exitCode=1;});
