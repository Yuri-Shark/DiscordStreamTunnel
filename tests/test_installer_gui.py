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
                    self.assertEqual(view.buttons['install']['state'],'disabled')
                    self.assertEqual(view.buttons['repair']['state'],'normal')
                    view.task('uninstall')
                    uninstall.assert_not_called()
                    view.file.set('synthetic.conf')
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
