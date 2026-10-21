const assert=require('node:assert/strict'),crypto=require('crypto'),zlib=require('zlib'),fs=require('fs'),os=require('os'),path=require('path');
const updater=require('../source/update.cjs'),repair=require('../source/repair.cjs');
const keys=crypto.generateKeyPairSync('ed25519');
const files=['source/app.py','source/native.cjs','source/stream_relay.py','source/runtime.template.js'].map(p=>({path:p,content:Buffer.from('synthetic-test').toString('base64'),sha256:crypto.createHash('sha256').update('synthetic-test').digest('hex')}));
const buffer=zlib.gzipSync(JSON.stringify({version:'1.2.0',files}));
const signed={schema:1,version:'1.2.0',url:'https://github.com/eduardofhabib/DiscordStreamTunnel/releases/download/v1.2.0/runtime-bundle.json.gz',bytes:buffer.length,sha256:crypto.createHash('sha256').update(buffer).digest('hex')};
const bytes=Buffer.from(JSON.stringify(signed)),envelope={signed:bytes.toString('base64'),signature:crypto.sign(null,bytes,keys.privateKey).toString('base64')};
assert.equal(updater.verify(envelope,keys.publicKey).version,'1.2.0');
const legacyBytes=Buffer.from(JSON.stringify({...signed,url:signed.url.replace('eduardofhabib/','SuporteAvyra/')}));
assert.equal(updater.verify({signed:legacyBytes.toString('base64'),signature:crypto.sign(null,legacyBytes,keys.privateKey).toString('base64')},keys.publicKey).version,'1.2.0');
assert.throws(()=>updater.verify({...envelope,signed:Buffer.from(JSON.stringify({...signed,version:'9.0.0'})).toString('base64')},keys.publicKey),/Signature/);
assert.throws(()=>updater.unpack(Buffer.concat([buffer,Buffer.from('tamper')]),signed),/Hash/);
const bad=zlib.gzipSync(JSON.stringify({version:'1.2.0',files:[...files,{...files[0],path:'source/../../private-core.json'}]}));
assert.throws(()=>updater.unpack(bad,{...signed,bytes:bad.length,sha256:crypto.createHash('sha256').update(bad).digest('hex')}),/Path/);
assert.equal(updater.newer('1.2.0','1.1.0'),true);assert.equal(updater.newer('1.0.0','1.1.0'),false);
const temp=fs.mkdtempSync(path.join(os.tmpdir(),'dts-update-test-'));
try{
 fs.mkdirSync(path.join(temp,'data'));const preload=path.join(temp,'preload.js'),original='// Vencord upstream new version\nvoid 0;\n';fs.writeFileSync(preload,original);fs.writeFileSync(path.join(temp,'installation.json'),JSON.stringify({preload}));
 assert.equal(repair.ensure(temp),true);assert.equal(repair.ensure(temp),false);
 assert.equal(repair.clean(fs.readFileSync(preload,'utf8')),original);
 fs.writeFileSync(preload,'// Vencord upstream replaced preload\n');repair.ensure(temp);
 assert.equal(repair.clean(fs.readFileSync(preload,'utf8')),'// Vencord upstream replaced preload\n');
 const bundle=updater.unpack(buffer,signed);updater.activate(temp,bundle);
 assert.equal(JSON.parse(fs.readFileSync(path.join(temp,'active.json'))).version,'1.2.0');
 assert.equal(fs.readFileSync(path.join(temp,'versions','1.2.0','source','app.py'),'utf8'),'synthetic-test');
 updater.activate(temp,bundle);assert.equal(JSON.parse(fs.readFileSync(path.join(temp,'active.json'))).version,'1.2.0');
}finally{
 if(path.dirname(temp)!==os.tmpdir()||!path.basename(temp).startsWith('dts-update-test-'))throw Error('Unsafe cleanup path');fs.rmSync(temp,{recursive:true,force:true});
}
console.log('OK: signed updates, rejection of tampering/traversal, version ordering, atomic activation and recovery after upstream preload replacement.');
