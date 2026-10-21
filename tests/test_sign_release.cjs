'use strict';
const assert=require('node:assert/strict'),crypto=require('crypto'),zlib=require('zlib');
const {createManifests}=require('../scripts/sign_release.cjs'),updater=require('../source/update.cjs');
const keys=crypto.generateKeyPairSync('ed25519');
const privateKey=keys.privateKey.export({type:'pkcs8',format:'pem'});
const encode=value=>{const bytes=Buffer.from(JSON.stringify(value));return {signed:bytes.toString('base64'),signature:crypto.sign(null,bytes,privateKey).toString('base64')};};
const files=['app.py','native.cjs','stream_relay.py','runtime.template.js'].map(name=>({path:'source/'+name,content:Buffer.from('test').toString('base64'),sha256:crypto.createHash('sha256').update('test').digest('hex')}));
const payload=zlib.gzipSync(JSON.stringify({version:'1.1.3',files}));
const previous={schema:1,version:'1.1.2',url:'https://github.com/previous-owner/DiscordStreamTunnel/releases/download/v1.1.2/runtime-bundle.json.gz'};
const options={payload,version:'1.1.3',repository:'Yuri-Shark/DiscordStreamTunnel',privateKey,compatEnvelope:encode(previous)};
const manifests=createManifests(options);
const canonical=updater.verify(manifests.canonical,keys.publicKey);
assert.equal(updater.unpack(payload,canonical).version,'1.1.3');
const compatBytes=Buffer.from(manifests.compatibility.signed,'base64');
assert(crypto.verify(null,compatBytes,keys.publicKey,Buffer.from(manifests.compatibility.signature,'base64')));
const compatibility=JSON.parse(compatBytes);
assert.equal(compatibility.url,'https://github.com/previous-owner/DiscordStreamTunnel/releases/download/v1.1.3/runtime-bundle.json.gz');
assert.equal(compatibility.sha256,canonical.sha256);
const installers={win32:{url:'https://github.com/Yuri-Shark/DiscordStreamTunnel/releases/download/v1.1.3/DiscordStreamTunnel-1.1.3-Windows-x64.zip',bytes:128,sha256:'a'.repeat(64)}};
const withInstaller=createManifests({...options,installers});
assert.deepEqual(updater.verify(withInstaller.canonical,keys.publicKey).installers,installers);
assert.deepEqual(JSON.parse(Buffer.from(withInstaller.compatibility.signed,'base64')).installers,installers);
assert.deepEqual(updater.unpack(payload,compatibility),updater.unpack(payload,canonical));
assert.throws(()=>createManifests({...options,compatEnvelope:{...options.compatEnvelope,signed:Buffer.from(JSON.stringify({...previous,url:'https://github.com/attacker/DiscordStreamTunnel/releases/download/v1.1.2/runtime-bundle.json.gz'})).toString('base64')}}),/Signature/);
for(const url of ['http://github.com/previous-owner/DiscordStreamTunnel/releases/download/v1.1.2/runtime-bundle.json.gz','https://github.com/previous-owner/OtherProject/releases/download/v1.1.2/runtime-bundle.json.gz','https://github.com/previous-owner/DiscordStreamTunnel/releases/download/v1.1.2/runtime-bundle.json.gz?redirect=evil']){
 assert.throws(()=>createManifests({...options,compatEnvelope:encode({...previous,url})}),/CompatibilityURL/);
}
console.log('OK: canonical and historical manifests authenticate identical code; historical URL cannot be replaced without a valid signature.');
