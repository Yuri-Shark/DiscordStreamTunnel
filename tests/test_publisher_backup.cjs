'use strict';
const assert=require('node:assert/strict'),crypto=require('node:crypto');
const {encrypt,decrypt,fingerprint}=require('../scripts/publisher_backup.cjs');
const key=crypto.generateKeyPairSync('ed25519'),other=crypto.generateKeyPairSync('ed25519');
const pem=key.privateKey.export({format:'pem',type:'pkcs8'}),pub=key.publicKey.export({format:'pem',type:'spki'});
const password='Synthetic backup test password 123!';
const envelope=encrypt(pem,password,pub);
assert(decrypt(JSON.parse(JSON.stringify(envelope)),password,pub).equals(key.privateKey));
assert.equal(fingerprint(pub).length,64);assert(!JSON.stringify(envelope).includes('PRIVATE KEY'));
assert.throws(()=>decrypt(envelope,'A different incorrect password',pub),/BackupAuthenticationFailed/);
for(const field of ['ciphertext','tag','nonce']){
 const changed=structuredClone(envelope),bytes=Buffer.from(changed[field],'base64');bytes[0]^=1;changed[field]=bytes.toString('base64');
 assert.throws(()=>decrypt(changed,password,pub),/BackupAuthenticationFailed/);
}
const changed=structuredClone(envelope);changed.createdAt='2000-01-01T00:00:00.000Z';
assert.throws(()=>decrypt(changed,password,pub),/BackupAuthenticationFailed/);
const huge=structuredClone(envelope);huge.kdf.N=2**30;assert.throws(()=>decrypt(huge,password,pub),/BackupAuthenticationFailed/);
assert.throws(()=>encrypt(pem,'short',pub),/PasswordTooShortOrLong/);
assert.throws(()=>encrypt(pem,password,other.publicKey.export({format:'pem',type:'spki'})),/PublisherKeyMismatch/);
assert.throws(()=>decrypt(envelope,password,other.publicKey.export({format:'pem',type:'spki'})),/BackupAuthenticationFailed/);
const unicode='Uma senha sintética com acentos e 🔐 123';
assert(decrypt(encrypt(pem,unicode,pub),unicode,pub).equals(key.privateKey));
console.log('OK: portable key round trip, password and tamper rejection, bounded scrypt, public-key match and Unicode passwords.');
