"""Disable stale ICMP errors on our own Winsock UDP sockets only."""
import ctypes
from ctypes import wintypes


def suppress_udp_resets(sock):
    # socket.ioctl accepts only a subset of Winsock commands in supported
    # Python versions. Use WSAIoctl directly, with pointer-sized SOCKET on x64.
    api=ctypes.WinDLL('Ws2_32',use_last_error=True)
    api.WSAIoctl.argtypes=[ctypes.c_size_t,wintypes.DWORD,ctypes.c_void_p,wintypes.DWORD,
        ctypes.c_void_p,wintypes.DWORD,ctypes.POINTER(wintypes.DWORD),ctypes.c_void_p,ctypes.c_void_p]
    api.WSAIoctl.restype=ctypes.c_int
    api.WSAGetLastError.argtypes=[];api.WSAGetLastError.restype=ctypes.c_int
    disabled=wintypes.BOOL(False);returned=wintypes.DWORD()
    # SIO_UDP_CONNRESET (ICMP port unreachable), SIO_UDP_NETRESET (TTL expiry).
    # Apply before the first receive: older Proactor loops stop reading after
    # these asynchronous errors. Actual path failure is checked by the relay.
    for command in (0x9800000c,0x9800000f):
        if api.WSAIoctl(sock.fileno(),command,ctypes.byref(disabled),ctypes.sizeof(disabled),
                None,0,ctypes.byref(returned),None,None):
            raise ctypes.WinError(api.WSAGetLastError())
