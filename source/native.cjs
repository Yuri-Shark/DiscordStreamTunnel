'use strict';
const fs=require('fs'),path=require('path'),cp=require('child_process'),http=require('http'),crypto=require('crypto');
function start(root){
 const {webFrame,contextBridge,ipcRenderer}=require('electron/renderer');
 if(location.protocol!=='https:'||location.hostname!=='discord.com')return;
 const data=path.join(root,'data');if(!fs.existsSync(path.join(data,'private-core.json')))return;
 const settings=JSON.parse(fs.readFileSync(path.join(data,'local-settings.json'),'utf8'));
 const installation=JSON.parse(fs.readFileSync(path.join(root,'installation.json'),'utf8'));
 const version=JSON.parse(fs.readFileSync(path.join(__dirname,'version.json'),'utf8')).version;
 const repair=()=>require('./repair.cjs').ensure(root);
 repair();
 // VencordBuild completes only after all four upstream files have been written.
 const originalInvoke=ipcRenderer.invoke;
 ipcRenderer.invoke=function(channel,...args){const result=Reflect.apply(originalInvoke,this,[channel,...args]);if(channel!=='VencordBuild')return result;return result.then(value=>{if(value?.ok!==false)repair();return value;});};
 let pending;
 function probe(endpoint='/health'){return new Promise(resolve=>{
  const request=http.get({hostname:'127.0.0.1',port:settings.relayPort,path:endpoint+'?nonce='+encodeURIComponent(settings.nonce),headers:{Origin:'https://discord.com'},timeout:7000},response=>{let body='';response.on('data',chunk=>{body+=chunk;if(body.length>8192)request.destroy();});response.on('end',()=>{try{const value=JSON.parse(body);resolve(response.statusCode===200&&value.service==='DiscordStreamTunnel'&&value.protocolVersion===1&&value.configurationReady);}catch{resolve(false);}});});request.once('error',()=>resolve(false));request.once('timeout',()=>{request.destroy();resolve(false);});
 });}
 async function launch(){
  if(await probe())return;
  const environment={...process.env,DTS_ROOT:root,PYTHONDONTWRITEBYTECODE:'1',PYINSTALLER_RESET_ENVIRONMENT:'1'};delete environment.PYTHONHOME;delete environment.PYTHONPATH;
  if(process.platform==='linux')delete environment.LD_LIBRARY_PATH;
  const command=installation.worker,args=[...(installation.workerPrefix||[]),'--root',root,'--version',version,'worker','--owner-pid',String(process.ppid),'--idle-exit'];
  const child=cp.spawn(command,args,{cwd:root,windowsHide:true,detached:true,stdio:'ignore',env:environment});child.on('error',()=>{});child.unref();
  for(let n=0;n<100;n++){if(await probe())return;await new Promise(r=>setTimeout(r,50));}throw Error('HelperUnavailable');
 }
 function ensureHelper(){if(!pending)pending=launch().finally(()=>{pending=undefined;});return pending;}
 contextBridge.exposeInMainWorld('DiscordProtonStreamsNative',{ensureHelper,rememberVideoGuard(value){if(value!==null&&(!Number.isInteger(value)||value<0||value>100))throw Error('InvalidVariant');const file=path.join(data,'video-guard-before.json');if(!fs.existsSync(file))fs.writeFileSync(file,JSON.stringify({variantId:value}),{mode:0o600});}});
 // Startup and helper readiness run in parallel. A failed warmup never blocks UI.
 ensureHelper().then(()=>probe('/warmup')).catch(()=>{});
 const notice=require('./update-ui.cjs').start({root,runningVersion:version,repair});
 notice.catch(()=>{});
 require('./update.cjs').check(root,String(process.ppid)).then(()=>notice.then(()=>webFrame.executeJavaScript('window.__discordStreamTunnelUpdateNotice?.refresh()'))).catch(()=>{});
 const runtime=fs.readFileSync(path.join(__dirname,'runtime.template.js'),'utf8').replace('/*__CONFIG__*/null',JSON.stringify({relayPort:settings.relayPort,nonce:settings.nonce}));
 const boot=`(async()=>{for(let n=0;n<150;n++){try{if(window.Vencord?.Webpack?.findStore('StreamRTCConnectionStore')){${runtime};window.__discordProtonStreams?.automatico(true);return;}}catch{}await new Promise(r=>setTimeout(r,100));}})()`;
 webFrame.executeJavaScript(boot).catch(()=>{});
}
module.exports={start};
