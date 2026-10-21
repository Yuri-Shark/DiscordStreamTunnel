"""Manage our Discord shortcuts with Windows Shell APIs, without a shell process."""
import ctypes
import json
import os
from pathlib import Path
import re
import shutil
import uuid


class ShellLink:
    """Small IShellLinkW/IPersistFile wrapper. No third-party COM runtime."""
    def __init__(self, path=None):
        if os.name != 'nt':
            raise OSError('Windows only')
        self.link = ctypes.c_void_p()
        self.persist = ctypes.c_void_p()
        self.ole = ctypes.OleDLL('ole32')
        self.ole.CoInitializeEx.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        result = self.ole.CoInitializeEx(None, 2)
        self.initialized = result in (0, 1)
        self.ole.CoCreateInstance.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong, ctypes.c_void_p, ctypes.c_void_p]
        try:
            self.ole.CoCreateInstance(self.guid('00021401-0000-0000-C000-000000000046'), None, 1,
                                     self.guid('000214F9-0000-0000-C000-000000000046'), ctypes.byref(self.link))
            self.call(self.link, 0, [ctypes.c_void_p, ctypes.c_void_p],
                      self.guid('0000010B-0000-0000-C000-000000000046'), ctypes.byref(self.persist))
            if path is not None:
                self.call(self.persist, 5, [ctypes.c_wchar_p, ctypes.c_ulong], str(Path(path).resolve()), 0)
        except Exception:
            self.close()
            raise

    @staticmethod
    def guid(value):
        return ctypes.create_string_buffer(uuid.UUID(value).bytes_le)

    @staticmethod
    def call(pointer, index, types, *args):
        table = ctypes.cast(pointer, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        function = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, *types)(table[index])
        result = function(pointer, *args)
        if result < 0:
            raise OSError('Windows Shell operation failed', hex(result & 0xffffffff))
        return result

    @property
    def target(self):
        value = ctypes.create_unicode_buffer(32768)
        self.call(self.link, 3, [ctypes.c_wchar_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_ulong], value, len(value), None, 0)
        return value.value

    @property
    def arguments(self):
        value = ctypes.create_unicode_buffer(32768)
        self.call(self.link, 10, [ctypes.c_wchar_p, ctypes.c_int], value, len(value))
        return value.value

    @property
    def icon(self):
        value = ctypes.create_unicode_buffer(32768)
        index = ctypes.c_int()
        self.call(self.link, 16, [ctypes.c_wchar_p, ctypes.c_int, ctypes.POINTER(ctypes.c_int)],
                  value, len(value), ctypes.byref(index))
        return value.value, index.value

    def set_icon(self, path, index=0):
        self.call(self.link, 17, [ctypes.c_wchar_p, ctypes.c_int], str(path), index)

    def update(self, target, arguments, directory):
        for index, value in ((20, target), (11, arguments), (9, directory)):
            self.call(self.link, index, [ctypes.c_wchar_p], str(value))

    def save(self, path):
        self.call(self.persist, 6, [ctypes.c_wchar_p, ctypes.c_int], str(Path(path).resolve()), 1)

    def close(self):
        for pointer in (self.persist, self.link):
            if pointer:
                table = ctypes.cast(pointer, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
                ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(table[2])(pointer)
                pointer.value = None
        if self.initialized:
            self.ole.CoUninitialize()
            self.initialized = False

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


def standard_folders():
    """Respect redirected/OneDrive desktops through SHGetFolderPathW."""
    buffer = ctypes.create_unicode_buffer(32768)
    shell = ctypes.WinDLL('shell32')
    shell.SHGetFolderPathW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_ulong, ctypes.c_wchar_p]
    folders = []
    for csidl in (0x10, 0x02):  # Desktop directory and Start Menu Programs.
        if shell.SHGetFolderPathW(None, csidl, None, 0, buffer) == 0:
            folders.append(Path(buffer.value))
    folders.append(Path(os.environ['APPDATA'])/'Microsoft/Internet Explorer/Quick Launch/User Pinned/TaskBar')
    return folders


def manage(root, remove=False, folders=None):
    root = Path(root).resolve()
    data = root/'data'
    record = data/'shortcuts.json'
    executable = root/'app/DiscordStreamTunnel.exe'
    folders = [Path(p).resolve() for p in (standard_folders() if folders is None else folders)]
    if remove:
        if not record.exists():
            return 0
        restored = 0
        for item in json.loads(record.read_text(encoding='utf-8-sig')):
            link, backup = Path(item['path']).resolve(), Path(item['backup']).resolve()
            if (link.name.lower() != 'discord.lnk' or not any(link.is_relative_to(p) for p in folders)
                    or not backup.is_relative_to(data/'shortcut-backups') or backup.suffix.lower() != '.lnk'):
                continue
            if link.is_file() and backup.is_file():
                with ShellLink(link) as current:
                    owned = os.path.normcase(current.target) == os.path.normcase(str(executable))
                if owned:
                    shutil.copy2(backup, link)
                    restored += 1
        return restored
    backups = data/'shortcut-backups'
    backups.mkdir(parents=True, exist_ok=True)
    changes = json.loads(record.read_text(encoding='utf-8-sig')) if record.exists() else []
    modified = 0
    candidates = sorted({file.resolve() for folder in folders if folder.is_dir() for file in folder.rglob('Discord.lnk')})
    for file in candidates:
        if not any(file.is_relative_to(folder) for folder in folders):continue
        with ShellLink(file) as shortcut:
            owned = os.path.normcase(shortcut.target) == os.path.normcase(str(executable))
            if owned:
                # Older installs changed the target while leaving an implicit icon.
                # Recover its original appearance without changing launch behavior.
                if not shortcut.icon[0]:
                    for item in changes:
                        if os.path.normcase(item['path']) != os.path.normcase(str(file)):
                            continue
                        backup = Path(item['backup']).resolve()
                        if (backup.is_relative_to(backups) and backup.suffix.lower() == '.lnk'
                                and backup.is_file()):
                            with ShellLink(backup) as original:
                                icon, index = original.icon
                                icon = icon or original.target
                            if icon:
                                shortcut.set_icon(icon, index)
                                shortcut.save(file)
                                modified += 1
                        break
                continue
            name = Path(shortcut.target).name.lower()
            if not (name == 'discord.exe' or name == 'update.exe' and
                    re.search(r'(?:^|\s)--processStart\s+"?Discord\.exe(?:"|\s|$)', shortcut.arguments, re.I)):
                continue
            backup = backups/(uuid.uuid4().hex+'.lnk')
            shutil.copy2(file, backup)
            changes = [item for item in changes if os.path.normcase(item['path'])!=os.path.normcase(str(file))]
            changes.append({'path': str(file), 'backup': str(backup)})
            # Save the recovery record before touching each original shortcut.
            temp = record.with_suffix('.new.json')
            temp.write_text(json.dumps(changes, indent=2), encoding='utf-8')
            temp.replace(record)
            # Freeze the effective original icon before redirecting the target.
            # Explicit custom icons (including resource indexes) stay untouched.
            icon, index = shortcut.icon
            if not icon:
                shortcut.set_icon(shortcut.target, index)
            shortcut.update(executable, '--root "'+str(root)+'" launch', root)
            shortcut.save(file)
            modified += 1
    if not changes:
        record.write_text('[]', encoding='utf-8')
    return modified
