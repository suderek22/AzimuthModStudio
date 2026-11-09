"""Bounded Windows message-pipe requests without CRT errno translation."""
import ctypes
import time
from ctypes import wintypes

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
ERROR_IO_PENDING = 997
ERROR_MORE_DATA = 234
ERROR_PIPE_BUSY = 231
ERROR_FILE_NOT_FOUND = 2
WAIT_TIMEOUT = 258


class _Offsets(ctypes.Structure):
    _fields_ = [("Offset", wintypes.DWORD), ("OffsetHigh", wintypes.DWORD)]


class _Position(ctypes.Union):
    _fields_ = [("offsets", _Offsets), ("Pointer", ctypes.c_void_p)]


class Overlapped(ctypes.Structure):
    _anonymous_ = ("position",)
    _fields_ = [("Internal", ctypes.c_size_t), ("InternalHigh", ctypes.c_size_t),
                ("position", _Position), ("hEvent", wintypes.HANDLE)]


kernel32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                                wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
kernel32.CreateFileW.restype = wintypes.HANDLE
kernel32.WaitNamedPipeW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD]
kernel32.WaitNamedPipeW.restype = wintypes.BOOL
kernel32.SetNamedPipeHandleState.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD),
                                            ctypes.c_void_p, ctypes.c_void_p]
kernel32.SetNamedPipeHandleState.restype = wintypes.BOOL
kernel32.CreateEventW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
kernel32.CreateEventW.restype = wintypes.HANDLE
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.CloseHandle.restype = wintypes.BOOL
kernel32.ReadFile.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
                              ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(Overlapped)]
kernel32.ReadFile.restype = wintypes.BOOL
kernel32.WriteFile.argtypes = kernel32.ReadFile.argtypes
kernel32.WriteFile.restype = wintypes.BOOL
kernel32.GetOverlappedResult.argtypes = [wintypes.HANDLE, ctypes.POINTER(Overlapped),
                                        ctypes.POINTER(wintypes.DWORD), wintypes.BOOL]
kernel32.GetOverlappedResult.restype = wintypes.BOOL
kernel32.CancelIoEx.argtypes = [wintypes.HANDLE, ctypes.POINTER(Overlapped)]
kernel32.CancelIoEx.restype = wintypes.BOOL
kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
kernel32.WaitForSingleObject.restype = wintypes.DWORD
kernel32.ResetEvent.argtypes = [wintypes.HANDLE]
kernel32.ResetEvent.restype = wintypes.BOOL


def _transfer(handle, event, buffer, length: int, deadline: float, writing: bool) -> tuple[int, bool]:
    overlapped = Overlapped()
    overlapped.hEvent = event
    transferred = wintypes.DWORD()
    operation = kernel32.WriteFile if writing else kernel32.ReadFile
    if not kernel32.ResetEvent(event):
        raise ctypes.WinError(ctypes.get_last_error())
    ok = operation(handle, buffer, length, ctypes.byref(transferred), ctypes.byref(overlapped))
    error = 0 if ok else ctypes.get_last_error()
    if error == ERROR_IO_PENDING:
        remaining = max(0, int((deadline - time.monotonic()) * 1000))
        wait = kernel32.WaitForSingleObject(event, remaining)
        if wait != 0:
            wait_error = ctypes.get_last_error()
            kernel32.CancelIoEx(handle, ctypes.byref(overlapped))
            # Keep the buffers alive until cancellation has completed.
            kernel32.GetOverlappedResult(handle, ctypes.byref(overlapped), ctypes.byref(transferred), True)
            if wait == WAIT_TIMEOUT:
                raise TimeoutError("Mod platform reply timed out; the command may have executed and was not retried.")
            raise ctypes.WinError(wait_error)
        ok = kernel32.GetOverlappedResult(handle, ctypes.byref(overlapped), ctypes.byref(transferred), False)
        error = 0 if ok else ctypes.get_last_error()
    elif error == ERROR_MORE_DATA and not writing:
        # An immediately completed overlapped message read need not fill the
        # synchronous byte-count argument on ERROR_MORE_DATA.
        kernel32.GetOverlappedResult(handle, ctypes.byref(overlapped), ctypes.byref(transferred), False)
    if error and not (error == ERROR_MORE_DATA and not writing):
        raise ctypes.WinError(error)
    return transferred.value, error == ERROR_MORE_DATA


def request(path: str, line: str, timeout: float = 1.5) -> str:
    if not isinstance(path, str) or not path.startswith("\\\\.\\pipe\\fh2mod-"):
        raise ValueError("Invalid local mod platform pipe name.")
    if not line.strip() or any(char in line for char in ("\0", "\r", "\n")):
        raise ValueError("A command must be a single nonempty line.")
    data = (line.strip() + "\n").encode("utf-8")
    if len(data) > 65536:
        raise ValueError("Command is too long.")
    deadline = time.monotonic() + timeout
    while True:
        handle = kernel32.CreateFileW(path, 0xC0000000, 0, None, 3, 0x40000000, None)
        if handle != INVALID_HANDLE_VALUE:
            break
        error = ctypes.get_last_error()
        if error not in (ERROR_PIPE_BUSY, ERROR_FILE_NOT_FOUND):
            raise ctypes.WinError(error)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            reason = "busy" if error == ERROR_PIPE_BUSY else "not ready or disconnected"
            raise TimeoutError("Mod platform pipe is %s; no command was sent." % reason)
        if error == ERROR_PIPE_BUSY:
            kernel32.WaitNamedPipeW(path, max(1, min(50, int(remaining * 1000))))
        else:
            time.sleep(min(0.02, remaining))
    event = None
    try:
        mode = wintypes.DWORD(2)  # PIPE_READMODE_MESSAGE
        if not kernel32.SetNamedPipeHandleState(handle, ctypes.byref(mode), None, None):
            raise ctypes.WinError(ctypes.get_last_error())
        event = kernel32.CreateEventW(None, True, False, None)
        if not event:
            raise ctypes.WinError(ctypes.get_last_error())
        message = ctypes.create_string_buffer(data)
        sent, _ = _transfer(handle, event, message, len(data), deadline, True)
        if sent != len(data):
            raise OSError("Incomplete command write; command was not retried.")
        chunks = []
        size = 0
        while True:
            buffer = ctypes.create_string_buffer(65536)
            count, more = _transfer(handle, event, buffer, len(buffer), deadline, False)
            chunks.append(buffer.raw[:count])
            size += count
            if size > 1024 * 1024:
                raise ValueError("Mod platform reply exceeds 1 MiB.")
            if not more:
                break
        text = b"".join(chunks).decode("utf-8", "replace").strip()
        if not text:
            raise OSError("Mod platform closed without a reply; command was not retried.")
        return text
    finally:
        if event:
            kernel32.CloseHandle(event)
        kernel32.CloseHandle(handle)
