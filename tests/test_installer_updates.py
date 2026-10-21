import base64
import hashlib
import io
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import installer_updates as updates
from signature_verify import verify


def archive(extra=None):
    buffer=io.BytesIO()
    prefix='DiscordStreamTunnel-1.3.0-Windows-x64/'
    with zipfile.ZipFile(buffer,'w') as output:
        output.writestr(prefix+'DiscordStreamTunnel.exe',b'synthetic executable; never executed')
        output.writestr(prefix+'_internal/runtime.dll',b'synthetic dependency')
        if extra:output.writestr(prefix+extra,b'unsafe')
    return buffer.getvalue()


class InstallerUpdateTests(unittest.TestCase):
    def test_openssl_rfc8032_vector_and_tampering(self):
        key=bytes.fromhex('d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a')
        signature=bytes.fromhex('e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e065224901555fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b')
        verify(key,signature,b'')
        with self.assertRaisesRegex(ValueError,'Signature'):verify(key,signature,b'tampered')

    @unittest.skipUnless(shutil.which('node'),'Node used to generate independent Ed25519 test signatures')
    def test_signed_download_persistent_cache_and_tamper_rejection(self):
        payload=archive()
        signed={'schema':1,'version':'1.3.0','installers':{'win32':{
            'url':'https://github.com/Yuri-Shark/DiscordStreamTunnel/releases/download/v1.3.0/DiscordStreamTunnel-1.3.0-Windows-x64.zip',
            'bytes':len(payload),'sha256':hashlib.sha256(payload).hexdigest()}}}
        script="const c=require('crypto'),fs=require('fs'),k=c.generateKeyPairSync('ed25519'),b=fs.readFileSync(0);process.stdout.write(JSON.stringify({trust:{publicKey:k.publicKey.export({type:'spki',format:'pem'})},envelope:{signed:b.toString('base64'),signature:c.sign(null,b,k.privateKey).toString('base64')}}));"
        fixture=json.loads(subprocess.check_output(['node','-e',script],input=json.dumps(signed).encode()))
        original=updates.authenticate
        authenticate=lambda envelope:original(envelope,fixture['trust'])
        release=authenticate(fixture['envelope'])
        changed={**fixture['envelope'],'signed':base64.b64encode(json.dumps({**signed,'version':'9.9.9'}).encode()).decode()}
        with self.assertRaisesRegex(ValueError,'Signature'):authenticate(changed)
        with tempfile.TemporaryDirectory(prefix='dts-installer-update-') as directory:
            cache=Path(directory)
            with patch.object(updates,'authenticate',side_effect=authenticate), patch.object(updates,'fetch',return_value=payload):
                executable=updates.prepare(release,cache=cache)
                self.assertEqual(updates.cached('1.2.0',cache=cache),executable)
                self.assertIsNone(updates.cached('1.3.0',cache=cache))
                executable.write_bytes(b'tampered')
                self.assertIsNone(updates.cached('1.2.0',cache=cache))
            with self.assertRaisesRegex(ValueError,'Hash'):updates.checked_archive(payload+b'tampered',release)
        malicious=archive('../../outside.exe')
        descriptor={**release,'artifact':{**release['artifact'],'bytes':len(malicious),'sha256':hashlib.sha256(malicious).hexdigest()}}
        with self.assertRaisesRegex(ValueError,'Path'):updates.checked_archive(malicious,descriptor)

    def test_download_host_restrictions_and_version_ordering(self):
        for url in ['http://github.com/file','https://example.com/file','https://github.com:8443/file','https://user:secret@github.com/file']:
            with self.assertRaises(ValueError):updates.trusted_url(url)
        self.assertTrue(updates.newer('1.10.0','1.2.0'))
        self.assertFalse(updates.newer('1.2.0','1.2.0'))
