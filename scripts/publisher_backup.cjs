'use strict';
// Portable authenticated key backup. All secrets enter/leave over private stdin/stdout.
const crypto=require('node:crypto');
const FORMAT='DiscordStreamTunnel.publisher-key-backup',REPO='Yuri-Shark/DiscordStreamTunnel';
const KDF={name:'scrypt',N:131072,r:8,p:1};
function fail(){throw Error('BackupAuthenticationFailed');}
function passwordBytes(password){
 if(typeof password!=='string'||[...password].length<16||Buffer.byteLength(password)>4096)throw Error('PasswordTooShortOrLong');
 return Buffer.from(password,'utf8');
}
function decode(value,length){
 if(typeof value!=='string'||value.length>8192)fail();
 const b=Buffer.from(value,'base64');if(b.toString('base64')!==value||(length!==undefined&&b.length!==length))fail();return b;
}
function publicKey(key){const p=crypto.createPublicKey(key);if(p.asymmetricKeyType!=='ed25519')fail();return p.export({type:'spki',format:'pem'});}
function fingerprint(pem){return crypto.createHash('sha256').update(crypto.createPublicKey(pem).export({type:'spki',format:'der'})).digest('hex');}
function header(e){
 if(!e||e.format!==FORMAT||e.version!==1||e.repository!==REPO||e.cipher!=='aes-256-gcm'||
  e.kdf?.name!==KDF.name||e.kdf.N!==KDF.N||e.kdf.r!==KDF.r||e.kdf.p!==KDF.p||
  typeof e.createdAt!=='string'||e.createdAt.length>40||typeof e.publicKey!=='string'||e.publicKey.length>256)fail();
 decode(e.kdf.salt,32);decode(e.nonce,12);
 return Buffer.from(JSON.stringify({format:e.format,version:e.version,repository:e.repository,cipher:e.cipher,
  kdf:{...KDF,salt:e.kdf.salt},nonce:e.nonce,publicKey:e.publicKey,createdAt:e.createdAt}));
}
function derive(password,salt){const p=passwordBytes(password);try{return crypto.scryptSync(p,salt,32,{...KDF,maxmem:256*1024*1024});}finally{p.fill(0);}}
function decrypt(envelope,password,expectedPublicKey){
 let key,plain;
 try{
  const aad=header(envelope),ciphertext=decode(envelope.ciphertext,48),tag=decode(envelope.tag,16);
  if(expectedPublicKey!==undefined&&publicKey(expectedPublicKey)!==envelope.publicKey)fail();
  key=derive(password,decode(envelope.kdf.salt,32));
  const decipher=crypto.createDecipheriv('aes-256-gcm',key,decode(envelope.nonce,12),{authTagLength:16});
  decipher.setAAD(aad);decipher.setAuthTag(tag);plain=Buffer.concat([decipher.update(ciphertext),decipher.final()]);
  const privateKey=crypto.createPrivateKey({key:plain,format:'der',type:'pkcs8'});
  if(publicKey(privateKey)!==envelope.publicKey)fail();return privateKey;
 }catch{fail();}finally{key?.fill(0);plain?.fill(0);}
}
function encrypt(privatePem,password,expectedPublicKey){
 const privateKey=crypto.createPrivateKey(privatePem),pem=publicKey(privateKey);
 if(privateKey.asymmetricKeyType!=='ed25519'||pem!==publicKey(expectedPublicKey))throw Error('PublisherKeyMismatch');
 const envelope={format:FORMAT,version:1,repository:REPO,cipher:'aes-256-gcm',kdf:{...KDF,salt:crypto.randomBytes(32).toString('base64')},
  nonce:crypto.randomBytes(12).toString('base64'),publicKey:pem,createdAt:new Date().toISOString()};
 let key,plain;
 try{
  key=derive(password,decode(envelope.kdf.salt,32));plain=privateKey.export({format:'der',type:'pkcs8'});
  if(plain.length!==48)throw Error('UnexpectedKeyFormat');
  const cipher=crypto.createCipheriv('aes-256-gcm',key,decode(envelope.nonce,12),{authTagLength:16});cipher.setAAD(header(envelope));
  envelope.ciphertext=Buffer.concat([cipher.update(plain),cipher.final()]).toString('base64');envelope.tag=cipher.getAuthTag().toString('base64');
  const recovered=decrypt(envelope,password,expectedPublicKey);
  if(!recovered.equals(privateKey))throw Error('BackupRoundTripFailed');return envelope;
 }finally{key?.fill(0);plain?.fill(0);}
}
module.exports={encrypt,decrypt,fingerprint};
if(require.main===module){
 let input='';process.stdin.setEncoding('utf8');
 process.stdin.on('data',chunk=>{input+=chunk;if(Buffer.byteLength(input)>65536){process.stderr.write('BackupInputTooLarge');process.exit(1);}});
 process.stdin.on('end',()=>{
  try{
   const r=JSON.parse(input);input='';let result;
   if(r.action==='encrypt'){const envelope=encrypt(r.privateKey,r.password,r.expectedPublicKey);result={envelope,fingerprint:fingerprint(envelope.publicKey)};}
   else if(r.action==='verify'||r.action==='decrypt'){
    const key=decrypt(r.envelope,r.password,r.expectedPublicKey);result={verified:true,fingerprint:fingerprint(publicKey(key))};
    if(r.action==='decrypt')result.privateKey=key.export({type:'pkcs8',format:'pem'});
   }else throw Error('InvalidBackupAction');
   r.password=r.privateKey=undefined;process.stdout.write(JSON.stringify(result));
  }catch{process.stderr.write('BackupOperationFailed');process.exitCode=1;}
 });
}
