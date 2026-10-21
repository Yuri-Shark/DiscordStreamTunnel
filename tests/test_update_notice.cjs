const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),os=require('node:os');
const source=fs.readFileSync(path.join(__dirname,'..','source','update-notice.js'),'utf8');
class Element{
 constructor(tag,text){this.tag=tag;this.textContent=text||'';this.style={};this.children=[];this.events={};this.attributes={};this.disabled=false;}
 append(...children){for(const child of children){child.parent=this;this.children.push(child);}}
 setAttribute(k,v){this.attributes[k]=v;}
 addEventListener(k,fn){this.events[k]=fn;}
 remove(){if(this.parent)this.parent.children=this.parent.children.filter(x=>x!==this);}
}
const tick=()=>new Promise(resolve=>setImmediate(resolve));
async function fixture(ready=true){
 const body=new Element('body'),timers=[];let calls=0,prepared=0,fail=false;
 const update={ready,runningVersion:'1.1.1',availableVersion:ready?'1.1.2':null,phase:ready?'installed-for-next-start':'current'};
 const ctx=vm.createContext({document:{body,createElement:tag=>new Element(tag)},setTimeout:fn=>{timers.push(fn);return timers.length;},clearTimeout(){},DiscordStreamTunnelUpdatesNative:{getState:()=>update,prepareRestart:version=>{prepared++;assert.equal(version,'1.1.2');if(fail)throw Error('NotReady');return{ready:true,version};}},DiscordNative:{app:{relaunch(){calls++;}}}});ctx.window=ctx;
 vm.runInContext(source,ctx);await tick();
 const actions=()=>body.children[0]?.children[3]?.children;
 return{ctx,body,actions,update,get calls(){return calls;},get prepared(){return prepared;},set fail(v){fail=v;}};
}
async function main(){
 const none=await fixture(false);assert.equal(none.body.children.length,0);assert.equal(none.calls,0);
 const f=await fixture();assert.equal(f.calls,0);assert.equal(f.body.children.length,1);const panel=f.body.children[0];
 assert.equal(panel.style.position,'fixed');assert.equal(panel.style.top,'16px');assert.equal(panel.style.right,'16px');
 assert.equal(f.actions()[1].textContent,'Reiniciar e aplicar');
 vm.runInContext(source,f.ctx);await tick();assert.equal(f.body.children.length,1);
 await f.actions()[1].events.click();await f.actions()[1].events.click();assert.equal(f.calls,1);assert.equal(f.prepared,1);
 const later=await fixture();later.actions()[0].events.click();await later.ctx.__discordStreamTunnelUpdateNotice.refresh();assert.equal(later.body.children.length,0);assert.equal(later.calls,0);
 const error=await fixture();error.fail=true;await error.actions()[1].events.click();assert.equal(error.calls,0);assert.equal(error.actions()[1].disabled,false);assert.equal(error.actions()[1].textContent,'Tentar novamente');
 const root=fs.mkdtempSync(path.join(os.tmpdir(),'dts-ui-state-'));
 try{
  const ui=require('../source/update-ui.cjs');fs.mkdirSync(path.join(root,'data'));fs.writeFileSync(path.join(root,'active.json'),JSON.stringify({version:'1.1.1'}));assert.equal(ui.state(root,'1.1.1').ready,false);
  const versionPath=path.join(root,'versions','1.1.2','source');fs.mkdirSync(versionPath,{recursive:true});
  fs.writeFileSync(path.join(root,'active.json'),JSON.stringify({version:'1.1.2'}));fs.writeFileSync(path.join(versionPath,'version.json'),JSON.stringify({version:'1.1.2'}));
  assert.equal(ui.state(root,'1.1.1').ready,false);
  for(const name of ['app.py','native.cjs','runtime.template.js'])fs.writeFileSync(path.join(versionPath,name),'synthetic');
  assert.equal(ui.state(root,'1.1.1').ready,true);assert.equal(ui.state(root,'1.1.2').ready,false);
  let bridge,repairs=0;
  const context=vm.createContext({module:{exports:{}},__dirname:path.join(__dirname,'..','source'),require:name=>name==='electron/renderer'?{contextBridge:{exposeInMainWorld(_name,value){bridge=value;}},webFrame:{executeJavaScript:()=>Promise.resolve()}}:require(name==='./update.cjs'?'../source/update.cjs':name)});
  vm.runInContext(fs.readFileSync(path.join(__dirname,'..','source','update-ui.cjs'),'utf8'),context);
  await context.module.exports.start({root,runningVersion:'1.1.1',repair(){repairs++;}});
  assert.throws(()=>bridge.prepareRestart('9.9.9'),/UpdateNotReady/);assert.equal(repairs,0);
  assert.equal(bridge.prepareRestart('1.1.2').ready,true);assert.equal(repairs,1);
 }finally{if(path.dirname(root)!==os.tmpdir()||!path.basename(root).startsWith('dts-ui-state-'))throw Error('Unsafe cleanup');fs.rmSync(root,{recursive:true,force:true});}
 console.log('OK: verified-update-only notice at top right; no automatic restart; Later dismisses; one click prepares/relaunches once; failed preparation preserves call.');
}
main().catch(error=>{console.error(error);process.exitCode=1;});
