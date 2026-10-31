"""Shell file drops for Tk on Windows, without an extra Tcl or native DLL."""
import ctypes
from ctypes import wintypes
import os


class FileDrop:
    def __init__(self, root, receive):
        self.root, self.receive = root, receive
        self.hwnd = None
        if os.name != 'nt':
            return
        self.user = ctypes.WinDLL('user32', use_last_error=True)
        self.shell = ctypes.WinDLL('shell32', use_last_error=True)
        pointer = ctypes.c_ssize_t
        self.proc_type = ctypes.WINFUNCTYPE(pointer, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
        self.set_proc = self.user.SetWindowLongPtrW if ctypes.sizeof(ctypes.c_void_p) == 8 else self.user.SetWindowLongW
        self.set_proc.argtypes = [wintypes.HWND, ctypes.c_int, pointer]
        self.set_proc.restype = pointer
        self.user.CallWindowProcW.argtypes = [pointer, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        self.user.CallWindowProcW.restype = pointer
        self.user.GetParent.argtypes = [wintypes.HWND]
        self.user.GetParent.restype = wintypes.HWND
        self.shell.DragAcceptFiles.argtypes = [wintypes.HWND, wintypes.BOOL]
        self.shell.DragQueryFileW.argtypes = [wintypes.HANDLE, wintypes.UINT, wintypes.LPWSTR, wintypes.UINT]
        self.shell.DragQueryFileW.restype = wintypes.UINT
        self.shell.DragFinish.argtypes = [wintypes.HANDLE]
        root.update_idletasks()
        self.hwnd = self.user.GetParent(root.winfo_id()) or root.winfo_id()
        self.callback = self.proc_type(self.message)
        ctypes.set_last_error(0)
        self.previous = self.set_proc(self.hwnd, -4, ctypes.cast(self.callback, ctypes.c_void_p).value)
        if not self.previous:
            self.hwnd = None
            raise ctypes.WinError(ctypes.get_last_error())
        self.shell.DragAcceptFiles(self.hwnd, True)
        root.bind('<Destroy>', self.destroy, add='+')

    def message(self, hwnd, message, wparam, lparam):
        if message == 0x0233:  # WM_DROPFILES; the shell owns this HDROP until DragFinish.
            paths = []
            try:
                count = self.shell.DragQueryFileW(wparam, 0xffffffff, None, 0)
                if count == 1:
                    length = self.shell.DragQueryFileW(wparam, 0, None, 0)
                    if 0 < length <= 32767:
                        value = ctypes.create_unicode_buffer(length + 1)
                        self.shell.DragQueryFileW(wparam, 0, value, length + 1)
                        paths.append(value.value)
                self.root.after_idle(lambda: self.receive(paths))
            finally:
                self.shell.DragFinish(wparam)
            return 0
        return self.user.CallWindowProcW(self.previous, hwnd, message, wparam, lparam)

    def destroy(self, event):
        if event.widget is self.root and self.hwnd:
            self.shell.DragAcceptFiles(self.hwnd, False)
            self.set_proc(self.hwnd, -4, self.previous)
            self.hwnd = None
