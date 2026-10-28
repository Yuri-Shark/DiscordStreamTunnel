import contextlib
import io
import json
from pathlib import Path
import runpy
import shutil
from types import SimpleNamespace
import tarfile
import tempfile
import unittest
from unittest.mock import patch
import zipfile


ROOT=Path(__file__).resolve().parents[1]
GUIDE='LEIA-ME-PRIMEIRO.html'


class PackageTests(unittest.TestCase):
    def test_offline_guide_is_a_release_asset_and_in_both_installation_packages(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'scripts').mkdir();(root/'source'/'vendor').mkdir(parents=True)
            shutil.copy2(ROOT/'scripts'/'package_release.py',root/'scripts'/'package_release.py')
            for name in ('README.md','SECURITY.md','LICENSE.txt','instalar.sh',GUIDE):shutil.copy2(ROOT/name,root/name)
            (root/'docs').mkdir();shutil.copy2(ROOT/'docs'/'ANALISE-CONEXAO.md',root/'docs'/'ANALISE-CONEXAO.md')
            (root/'source'/'version.json').write_text('{"version":"1.3.0"}',encoding='utf-8')
            (root/'source'/'diagnostics.py').write_text('# synthetic module',encoding='utf-8')
            (root/'dist'/'DiscordStreamTunnel').mkdir(parents=True)
            (root/'dist'/'DiscordStreamTunnel'/'DiscordStreamTunnel.exe').write_bytes(b'synthetic executable, never executed')
            python=root/'python';python.mkdir();(python/'LICENSE.txt').write_text('license',encoding='utf-8')
            for component in ('tk','tcl'):
                library=python/'tcl'/(component+'8.6');library.mkdir(parents=True)
                (library/'license.terms').write_text('license',encoding='utf-8')
            metadata=SimpleNamespace(files=[])
            with patch('sys.base_prefix',str(python)),patch('subprocess.check_output',side_effect=['synthetic-commit','']), \
                    patch('importlib.metadata.distribution',return_value=metadata),contextlib.redirect_stdout(io.StringIO()):
                runpy.run_path(str(root/'scripts'/'package_release.py'))
            expected=(ROOT/GUIDE).read_bytes().replace(b'\r\n',b'\n')
            self.assertEqual((root/'release'/GUIDE).read_bytes().replace(b'\r\n',b'\n'),expected)
            with zipfile.ZipFile(root/'release'/'DiscordStreamTunnel-1.3.0-Windows-x64.zip') as archive:
                self.assertEqual(archive.read('DiscordStreamTunnel-1.3.0-Windows-x64/'+GUIDE).replace(b'\r\n',b'\n'),expected)
                self.assertIn('DiscordStreamTunnel-1.3.0-Windows-x64/docs/ANALISE-CONEXAO.md',archive.namelist())
            with tarfile.open(root/'release'/'DiscordStreamTunnel-1.3.0-linux.tar.gz') as archive:
                self.assertEqual(archive.extractfile('DiscordStreamTunnel-1.3.0-linux/'+GUIDE).read(),expected)
            html=expected.decode('utf-8')
            self.assertNotIn('<script',html);self.assertNotIn('<link',html)
            self.assertIn('charset="utf-8"',html)


if __name__=='__main__':unittest.main()
