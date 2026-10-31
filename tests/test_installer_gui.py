import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch


@unittest.skipUnless(os.name == 'nt', 'Windows GUI; Linux CLI remains available')
class InstallerGuiTests(unittest.TestCase):
    def test_async_actions_are_guarded_and_uninstall_can_be_cancelled(self):
        import tkinter as tk
        import installer_gui as gui
        state = {'vencordFound':True,'integrationInstalled':True,'configurationPresent':True,'corePresent':True}
        with tempfile.TemporaryDirectory(prefix='dts-gui-') as directory:
            profile = Path(directory)
            with patch.object(gui,'ROOT',profile), patch.object(gui,'status',return_value=state), patch.object(gui.InstallerWindow,'start_update_check'), \
                    patch.object(gui,'target_preload',return_value=profile/'Vencord/dist/preload.js'), \
                    patch.object(gui,'install') as install, patch.object(gui,'uninstall') as uninstall, \
                    patch.object(gui.messagebox,'askyesno',return_value=False):
                root = tk.Tk()
                root.withdraw()
                try:
                    view = gui.InstallerWindow(root)
                    view.initial_status()
                    self.assertEqual(view.buttons['install']['state'],'normal')
                    view.task('install')
                    install.assert_not_called()
                    self.assertEqual(view.entry['highlightbackground'],gui.RED)
                    self.assertEqual(view.buttons['repair']['state'],'normal')
                    view.task('uninstall')
                    uninstall.assert_not_called()
                    config=profile/'synthetic.conf';config.write_text('synthetic')
                    view.receive_drop([str(config)])
                    self.assertEqual(view.file.get(),str(config))
                    self.assertEqual(view.entry['highlightbackground'],gui.BORDER)
                    self.assertEqual(view.buttons['install']['state'],'normal')
                    install.side_effect = gui.InstallError('Configuração inválida. Escolha outro arquivo.')
                    view.task('install')
                    self.assertTrue(view.busy)
                    self.assertEqual(view.buttons['uninstall']['state'],'disabled')
                    view.task('install')  # A second click must not start a second installation.
                    deadline = time.monotonic()+3
                    while view.busy and time.monotonic()<deadline:
                        root.update()
                        time.sleep(.01)
                    self.assertFalse(view.busy)
                    install.assert_called_once()
                    self.assertEqual(view.result_title.get(),'Não foi possível concluir')
                    self.assertEqual(view.message.get(),'Configuração inválida. Escolha outro arquivo.')
                    self.assertEqual(view.buttons['status']['state'],'normal')
                finally:
                    root.destroy()

    def test_missing_vencord_and_invalid_drop_highlight_without_installing(self):
        import tkinter as tk
        import installer_gui as gui
        state={'vencordFound':False,'integrationInstalled':False}
        with tempfile.TemporaryDirectory(prefix='dts-gui-') as directory, \
                patch.object(gui,'status',return_value=state),patch.object(gui.InstallerWindow,'start_update_check'),patch.object(gui,'install') as install:
            root=tk.Tk();root.withdraw()
            try:
                view=gui.InstallerWindow(root);view.initial_status();view.task('install')
                self.assertEqual(view.detected['highlightbackground'],gui.RED)
                self.assertEqual(view.entry['highlightbackground'],gui.RED)
                bad=Path(directory)/'not-a-config.txt';bad.write_text('synthetic')
                view.receive_drop([str(bad)])
                self.assertEqual(view.file.get(),'')
                good=Path(directory)/'configuração com espaços.CONF';good.write_text('synthetic')
                view.receive_drop([str(good)])
                self.assertEqual(view.entry['highlightbackground'],gui.BORDER)
                self.assertEqual(view.detected['highlightbackground'],gui.RED)
                view.task('install');install.assert_not_called()
                view.busy=True;view.receive_drop([str(bad)]);self.assertEqual(view.file.get(),str(good))
            finally:root.destroy()

    def test_windows_shell_drop_reaches_the_tk_window(self):
        import ctypes
        from ctypes import wintypes
        import struct
        import tkinter as tk
        from windows_drop import FileDrop
        root=tk.Tk();root.withdraw();received=[]
        try:
            drop=FileDrop(root,received.append)
            kernel=ctypes.WinDLL('kernel32',use_last_error=True)
            kernel.GlobalAlloc.argtypes=[wintypes.UINT,ctypes.c_size_t];kernel.GlobalAlloc.restype=wintypes.HANDLE
            kernel.GlobalLock.argtypes=[wintypes.HANDLE];kernel.GlobalLock.restype=ctypes.c_void_p
            kernel.GlobalUnlock.argtypes=[wintypes.HANDLE]
            path='C:\\Configurações pessoais\\Proton conta.CONF'
            raw=struct.pack('<IiiII',20,0,0,0,1)+(path+'\0\0').encode('utf-16-le')
            handle=kernel.GlobalAlloc(0x0042,len(raw));self.assertTrue(handle)
            pointer=kernel.GlobalLock(handle);ctypes.memmove(pointer,raw,len(raw));kernel.GlobalUnlock(handle)
            user=ctypes.WinDLL('user32',use_last_error=True)
            user.SendMessageW.argtypes=[wintypes.HWND,wintypes.UINT,wintypes.WPARAM,wintypes.LPARAM]
            user.SendMessageW(drop.hwnd,0x0233,handle,0)
            root.update()
            self.assertEqual(received,[[path]])
        finally:root.destroy()
