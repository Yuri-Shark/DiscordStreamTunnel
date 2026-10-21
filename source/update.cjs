'use strict';
const fs=require('fs'),path=require('path'),https=require('https'),crypto=require('crypto'),zlib=require('zlib');
const REPO='eduardofhabib/DiscordStreamTunnel';
// One signed migration release uses the previous URL, which GitHub redirects
// after ownership transfer. This lets 1.1.0 clients migrate automatically.
const LEGACY_REPO='SuporteAvyra/DiscordStreamTunnel';
const version=/^\d+\.\d+\.\d+$/;
function newer(a,b){const x=a.split('.').map(Number),y=b.split('.').map(Number);for(let n=0;n<3;n++){if(x[n]!==y[n])return x[n]>y[n];}return false;}
function verify(envelope,key){
 const bytes=Buffer.from(envelope.signed,'base64');
 if(!crypto.verify(null,bytes,key,Buffer.from(envelope.signature,'base64')))throw Error('InvalidSignature');
 const signed=JSON.parse(bytes);
 if(signed.schema!==1||!version.test(signed.version)||!Number.isInteger(signed.bytes)||signed.bytes<1||signed.bytes>4*1024*1024||!/^[a-f0-9]{64}$/.test(signed.sha256))throw Error('InvalidManifest');
 const u=new URL(signed.url);if(u.protocol!=='https:'||u.hostname!=='github.com'||![REPO,LEGACY_REPO].some(repo=>u.pathname.startsWith('/'+repo+'/releases/download/'))||u.username||u.password)throw Error('UnexpectedReleaseURL');
 return signed;
}
function unpack(buffer,signed){
 if(buffer.length!==signed.bytes||crypto.createHash('sha256').update(buffer).digest('hex')!==signed.sha256)throw Error('InvalidPayloadHash');
 const bundle=JSON.parse(zlib.gunzipSync(buffer,{maxOutputLength:16*1024*1024}));
 if(bundle.version!==signed.version||!Array.isArray(bundle.files)||bundle.files.length>500)throw Error('InvalidBundle');
 const names=new Set();
 for(const file of bundle.files){
  if(typeof file.path!=='string'||!file.path.startsWith('source/')||file.path.includes('\\')||file.path.split('/').some(p=>!p||p==='.'||p==='..'||!/^[a-zA-Z0-9_.-]+$/.test(p))||names.has(file.path)||!/\.(py|pyi|js|cjs|json|txt|typed|ps1|c)$/.test(file.path)&&!/(LICENSE|METADATA|RECORD|WHEEL|zip-safe|top_level.txt)$/.test(file.path))throw Error('InvalidBundlePath');
  names.add(file.path);file.bytes=Buffer.from(file.content,'base64');if(crypto.createHash('sha256').update(file.bytes).digest('hex')!==file.sha256)throw Error('InvalidFileHash');
 }
 for(const name of ['source/app.py','source/native.cjs','source/stream_relay.py','source/runtime.template.js'])if(!names.has(name))throw Error('IncompleteBundle');
 return bundle;
}
function get(url,limit,redirects=0){return new Promise((resolve,reject)=>{
 const u=new URL(url);if(u.protocol!=='https:'||!['github.com','release-assets.githubusercontent.com','objects.githubusercontent.com'].includes(u.hostname)||redirects>5)return reject(Error('UnexpectedDownloadHost'));
 const request=https.get(u,{headers:{'User-Agent':'DiscordStreamTunnel/1.1','Accept':'application/octet-stream'},timeout:5000},response=>{
  if([301,302,303,307,308].includes(response.statusCode)){response.resume();return get(new URL(response.headers.location,u).href,limit,redirects+1).then(resolve,reject);}
  if(response.statusCode!==200){response.resume();return reject(Error('HTTP'+response.statusCode));}
  const chunks=[];let size=0;response.on('data',chunk=>{size+=chunk.length;if(size>limit)request.destroy(Error('DownloadTooLarge'));else chunks.push(chunk);});response.on('end',()=>resolve(Buffer.concat(chunks)));response.on('error',reject);
 });request.once('error',reject);request.once('timeout',()=>request.destroy(Error('DownloadTimeout')));
});}
function activate(root,bundle){
 const versions=path.resolve(root,'versions'),dest=path.join(versions,bundle.version),staging=path.join(versions,'.staging-'+bundle.version+'-'+crypto.randomUUID());
 fs.mkdirSync(staging,{recursive:true,mode:0o700});
 try{for(const file of bundle.files){const target=path.resolve(staging,file.path);if(!target.startsWith(staging+path.sep))throw Error('UnsafePath');fs.mkdirSync(path.dirname(target),{recursive:true});fs.writeFileSync(target,file.bytes,{mode:0o600});}
  if(fs.existsSync(dest)){
   for(const file of bundle.files){const existing=path.join(dest,file.path);if(!fs.existsSync(existing)||crypto.createHash('sha256').update(fs.readFileSync(existing)).digest('hex')!==file.sha256)throw Error('VersionAlreadyExists');}
  }else fs.renameSync(staging,dest);
  const temp=path.join(root,'active.new.json');fs.writeFileSync(temp,JSON.stringify({version:bundle.version}),{mode:0o600});fs.renameSync(temp,path.join(root,'active.json'));
 }finally{if(path.dirname(staging)===versions&&path.basename(staging).startsWith('.staging-')&&fs.existsSync(staging))fs.rmSync(staging,{recursive:true,force:true});}
}
async function check(root,session){
 const status=path.join(root,'data','update-status.json');
 try{if(JSON.parse(fs.readFileSync(status)).session===session)return;}catch{}
 const write=value=>{const temp=status+'.new';fs.writeFileSync(temp,JSON.stringify({session,checkedAt:new Date().toISOString(),...value}),{mode:0o600});fs.renameSync(temp,status);};
 write({state:'checking'});
 try{
  const trust=JSON.parse(fs.readFileSync(path.join(root,'trust.json'),'utf8'));
  if(trust.repository===LEGACY_REPO){trust.repository=REPO;const temp=path.join(root,'trust.new.json');fs.writeFileSync(temp,JSON.stringify(trust),{mode:0o600});fs.renameSync(temp,path.join(root,'trust.json'));}
  const envelope=JSON.parse(await get('https://github.com/'+REPO+'/releases/latest/download/update-manifest.json',65536));
  const signed=verify(envelope,trust.publicKey),current=JSON.parse(fs.readFileSync(path.join(root,'active.json'),'utf8')).version;
  if(!newer(signed.version,current)){write({state:'current',version:current});return;}
  const bundle=unpack(await get(signed.url,signed.bytes),signed);activate(root,bundle);
  write({state:'installed-for-next-start',version:signed.version});
 }catch(error){write({state:'kept-current-version',errorClass:error.message.replace(/[^a-zA-Z0-9]/g,'').slice(0,80)});}
}
module.exports={check,verify,unpack,activate,newer};
