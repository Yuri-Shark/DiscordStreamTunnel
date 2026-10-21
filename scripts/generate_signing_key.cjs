const fs=require('fs'),path=require('path'),crypto=require('crypto');
const directory=process.argv[2];if(!directory)throw Error('Supply private publisher directory outside repository');
fs.mkdirSync(directory,{recursive:true,mode:0o700});
const privatePath=path.join(directory,'release-signing-private.pem');
if(!fs.existsSync(privatePath)){const keys=crypto.generateKeyPairSync('ed25519');fs.writeFileSync(privatePath,keys.privateKey.export({type:'pkcs8',format:'pem'}),{mode:0o600});}
const publicKey=crypto.createPublicKey(fs.readFileSync(privatePath)).export({type:'spki',format:'pem'});
fs.writeFileSync(path.join(__dirname,'..','source','trust.json'),JSON.stringify({repository:'eduardofhabib/DiscordStreamTunnel',publicKey},null,2));
process.stdout.write('Public update verification key generated. Private signing key stays outside repository.\n');
