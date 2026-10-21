'use strict';
const fs=require('fs'),path=require('path'),crypto=require('crypto');

function createManifests({payload,version,repository,privateKey,compatEnvelope}){
 const publicKey=crypto.createPublicKey(privateKey);
 if(!/^\d+\.\d+\.\d+$/.test(version)||!/^[-\w]+\/DiscordStreamTunnel$/.test(repository))throw Error('InvalidRelease');
 // Derive the historical URL from a previously signed release, keeping previous
 // account names out of the current source and runtime. Preserve the signing key.
 const previousBytes=Buffer.from(compatEnvelope.signed,'base64');
 if(!crypto.verify(null,previousBytes,publicKey,Buffer.from(compatEnvelope.signature,'base64')))throw Error('InvalidCompatibilitySignature');
 const previous=JSON.parse(previousBytes),oldURL=new URL(previous.url);
 if(previous.schema!==1||!/^\d+\.\d+\.\d+$/.test(previous.version)||oldURL.origin!=='https://github.com'||oldURL.username||oldURL.password||oldURL.search||oldURL.hash||!/^\/[-\w]+\/DiscordStreamTunnel\/releases\/download\/v\d+\.\d+\.\d+\/runtime-bundle\.json\.gz$/.test(oldURL.pathname))throw Error('InvalidCompatibilityURL');
 const compatibilityRepository=oldURL.pathname.split('/').slice(1,3).join('/');
 const sign=repo=>{
  const signed={schema:1,version,url:'https://github.com/'+repo+'/releases/download/v'+version+'/runtime-bundle.json.gz',bytes:payload.length,sha256:crypto.createHash('sha256').update(payload).digest('hex')};
  const bytes=Buffer.from(JSON.stringify(signed));
  return {signed:bytes.toString('base64'),signature:crypto.sign(null,bytes,privateKey).toString('base64')};
 };
 return {canonical:sign(repository),compatibility:sign(compatibilityRepository)};
}

function main(){
 const privateKeyPath=process.argv[2],flag=process.argv.indexOf('--compat-manifest'),compatPath=flag>=0?process.argv[flag+1]:null;
 if(!privateKeyPath||!compatPath)throw Error('Supply private signing key and --compat-manifest PATH to the previous signed update-manifest.json, both outside the repository');
 const root=path.resolve(__dirname,'..'),trust=JSON.parse(fs.readFileSync(path.join(root,'source','trust.json')));
 const version=JSON.parse(fs.readFileSync(path.join(root,'source','version.json'))).version;
 const payload=fs.readFileSync(path.join(root,'release','runtime-bundle.json.gz'));
 const privateKey=fs.readFileSync(privateKeyPath);
 if(crypto.createPublicKey(privateKey).export({type:'spki',format:'pem'})!==trust.publicKey)throw Error('PublisherKeyMismatch');
 const manifests=createManifests({payload,version,repository:trust.repository,privateKey,compatEnvelope:JSON.parse(fs.readFileSync(compatPath))});
 const updater=require('../source/update.cjs');updater.unpack(payload,updater.verify(manifests.canonical,trust.publicKey));
 fs.writeFileSync(path.join(root,'release','update-manifest-v2.json'),JSON.stringify(manifests.canonical,null,2));
 fs.writeFileSync(path.join(root,'release','update-manifest.json'),JSON.stringify(manifests.compatibility,null,2));
 console.log('Canonical and compatibility manifests signed and verified with the existing publisher key.');
}
if(require.main===module)main();
module.exports={createManifests};
