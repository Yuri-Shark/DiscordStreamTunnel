import json
import os
from pathlib import Path
import tempfile
import unittest

from windows_shortcuts import ShellLink, manage


@unittest.skipUnless(os.name == 'nt', 'Windows Shell API')
class ShortcutTests(unittest.TestCase):
    def test_native_shortcuts_backup_restore_and_ptb_preservation(self):
        with tempfile.TemporaryDirectory(prefix='dts-shortcuts-') as directory:
            base = Path(directory)
            root, desktop = base/'private-install', base/'Desktop'
            desktop.mkdir()
            stable, ptb = desktop/'Discord.lnk', desktop/'PTB/Discord.lnk'
            ptb.parent.mkdir()
            with ShellLink() as link:
                link.update(base/'Discord/Update.exe', '--processStart "Discord.exe"', base)
                link.save(stable)
            with ShellLink() as link:
                link.update(base/'DiscordPTB/DiscordPTB.exe', '', base)
                link.save(ptb)
            original, untouched = stable.read_bytes(), ptb.read_bytes()
            self.assertEqual(manage(root, folders=[desktop]), 1)
            with ShellLink(stable) as link:
                self.assertEqual(Path(link.target).resolve(), (root/'app/DiscordStreamTunnel.exe').resolve())
                self.assertIn(' launch', link.arguments)
            self.assertEqual(ptb.read_bytes(), untouched)
            self.assertEqual(manage(root, folders=[desktop]), 0)
            # Old PowerShell records were UTF-8 with a BOM; removal remains compatible.
            record = root/'data/shortcuts.json'
            content = json.loads(record.read_text())
            record.write_text(json.dumps(content), encoding='utf-8-sig')
            self.assertEqual(manage(root, remove=True, folders=[desktop]), 1)
            self.assertEqual(stable.read_bytes(), original)
            self.assertEqual(ptb.read_bytes(), untouched)
            self.assertEqual(manage(root, folders=[desktop]), 1)
            self.assertEqual(manage(root, remove=True, folders=[desktop]), 1)
            self.assertEqual(stable.read_bytes(), original)

    def test_uninstall_preserves_shortcuts_changed_by_the_user(self):
        with tempfile.TemporaryDirectory(prefix='dts-shortcuts-') as directory:
            base = Path(directory)
            root, desktop = base/'install', base/'Desktop'
            desktop.mkdir()
            path = desktop/'Discord.lnk'
            with ShellLink() as link:
                link.update(base/'Discord.exe', '', base)
                link.save(path)
            manage(root, folders=[desktop])
            with ShellLink(path) as link:
                link.update(base/'Other.exe', '', base)
                link.save(path)
            changed = path.read_bytes()
            self.assertEqual(manage(root, remove=True, folders=[desktop]), 0)
            self.assertEqual(path.read_bytes(), changed)

    def test_implicit_discord_icon_survives_target_redirection(self):
        with tempfile.TemporaryDirectory(prefix='dts-icons-') as directory:
            base = Path(directory)
            root, desktop = base/'install', base/'Desktop'
            desktop.mkdir()
            path, target = desktop/'Discord.lnk', base/'Discord/Discord.exe'
            with ShellLink() as link:
                link.update(target, '', base)
                link.save(path)
            original = path.read_bytes()
            self.assertEqual(manage(root, folders=[desktop]), 1)
            with ShellLink(path) as link:
                icon, index = link.icon
                self.assertEqual(Path(icon).resolve(), target.resolve())
                self.assertEqual(index, 0)
            self.assertEqual(manage(root, folders=[desktop]), 0)
            self.assertEqual(manage(root, remove=True, folders=[desktop]), 1)
            self.assertEqual(path.read_bytes(), original)

    def test_explicit_custom_icon_and_resource_index_are_preserved(self):
        with tempfile.TemporaryDirectory(prefix='dts-icons-') as directory:
            base = Path(directory)
            root, desktop = base/'install', base/'Desktop'
            desktop.mkdir()
            path = desktop/'Discord.lnk'
            with ShellLink() as link:
                link.update(base/'Discord/Update.exe', '--processStart Discord.exe', base)
                link.set_icon(base/'custom.dll', -42)
                link.save(path)
            with ShellLink(path) as link:
                original_icon = link.icon
            self.assertEqual(manage(root, folders=[desktop]), 1)
            with ShellLink(path) as link:
                self.assertEqual(link.icon, original_icon)
            self.assertEqual(manage(root, folders=[desktop]), 0)

    def test_old_install_missing_icon_is_repaired_without_changing_launch(self):
        with tempfile.TemporaryDirectory(prefix='dts-icons-') as directory:
            base = Path(directory)
            root, desktop = base/'install', base/'Desktop'
            desktop.mkdir()
            path, target = desktop/'Discord.lnk', base/'Discord/Discord.exe'
            with ShellLink() as link:
                link.update(target, '', base)
                link.save(path)
            original = path.read_bytes()
            manage(root, folders=[desktop])
            # Reproduce the pre-fix installation, including its existing backup.
            with ShellLink(path) as link:
                link.set_icon('')
                link.save(path)
                launch = link.target, link.arguments
            record = (root/'data/shortcuts.json').read_bytes()
            self.assertEqual(manage(root, folders=[desktop]), 1)
            with ShellLink(path) as link:
                self.assertEqual((link.target, link.arguments), launch)
                self.assertEqual(Path(link.icon[0]).resolve(), target.resolve())
            self.assertEqual((root/'data/shortcuts.json').read_bytes(), record)
            self.assertEqual(manage(root, folders=[desktop]), 0)
            self.assertEqual(manage(root, remove=True, folders=[desktop]), 1)
            self.assertEqual(path.read_bytes(), original)
