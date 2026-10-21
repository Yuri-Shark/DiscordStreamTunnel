'use strict';
const fs=require('fs'),path=require('path');
const {newer}=require('./update.cjs');
function state(root,runningVersion){
 const active=JSON.parse(fs.readFileSync(path.join(root,'active.json'),'utf8'));
 let phase='current';try{phase=JSON.parse(fs.readFileSync(path.join(root,'data','update-status.json'),'utf8')).state;}catch{}
 let ready=false;
 if(/^\d+\.\d+\.\d+$/.test(active.version)&&newer(active.version,runningVersion)){
  const source=path.join(root,'versions',active.version,'source');
  const version=JSON.parse(fs.readFileSync(path.join(source,'version.json'),'utf8')).version;
  ready=version===active.version&&['app.py','native.cjs','runtime.template.js'].every(name=>fs.existsSync(path.join(source,name)));
 }
 return{runningVersion,availableVersion:ready?active.version:null,ready,phase};
}
function start({root,runningVersion,repair}){
 const {webFrame,contextBridge}=require('electron/renderer');
 contextBridge.exposeInMainWorld('DiscordStreamTunnelUpdatesNative',{
  getState(){return state(root,runningVersion);},
  prepareRestart(expectedVersion){const update=state(root,runningVersion);if(!update.ready||update.availableVersion!==expectedVersion)throw Error('UpdateNotReady');repair();return{ready:true,version:update.availableVersion};}
 });
 return webFrame.executeJavaScript(fs.readFileSync(path.join(__dirname,'update-notice.js'),'utf8'));
}
module.exports={start,state};
