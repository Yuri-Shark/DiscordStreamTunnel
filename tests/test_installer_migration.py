import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import installation


class InstallerMigrationTests(unittest.TestCase):
    def test_repair_with_new_installer_migrates_code_and_key_without_conf(self):
        with tempfile.TemporaryDirectory(prefix='dts-migrate-') as directory:
            root=Path(directory);data=root/'data';data.mkdir()
            source=root/'package-source';source.mkdir()
            for name,content in {'app.py':'# new code','trust.json':'{"publicKey":"new-public-test-key"}', 'bootstrap.cjs':'// new bootstrap'}.items():
                (source/name).write_text(content)
            preload=root/'preload.js';preload.write_bytes(b'// Vencord updated upstream\n')
            (root/'installation.json').write_text(json.dumps({'preload':str(preload)}))
            (root/'active.json').write_text('{"version":"1.2.4"}')
            config=data/'private-core.json';config.write_bytes(b'local private configuration must remain unchanged')
            with patch.object(installation,'ROOT',root),patch.object(installation,'BASE',data),patch.object(installation,'RESOURCE',source),patch.object(installation,'VERSION','1.4.0'):
                installation.repair()
                self.assertEqual(json.loads((root/'active.json').read_text())['version'],'1.4.0')
                self.assertEqual((root/'versions/1.4.0/source/app.py').read_text(),'# new code')
                self.assertEqual(json.loads((root/'trust.json').read_text())['publicKey'],'new-public-test-key')
                self.assertEqual(config.read_bytes(),b'local private configuration must remain unchanged')
                self.assertIn(installation.START,preload.read_bytes())
                (root/'active.json').write_text('{"version":"9.0.0"}')
                installation.repair()
                self.assertEqual(json.loads((root/'active.json').read_text())['version'],'9.0.0','an old installer cannot downgrade the runtime')
