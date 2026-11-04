"""Windows Core Audio volume scoped to the wrapper process, not the system."""
import ctypes
import math
import uuid
from contextlib import contextmanager
from ctypes import wintypes

ole32 = ctypes.OleDLL("ole32")
HRESULT = ctypes.c_long
GUID = ctypes.c_ubyte * 16
ole32.CoInitializeEx.argtypes = [ctypes.c_void_p, wintypes.DWORD]
ole32.CoInitializeEx.restype = HRESULT
ole32.CoUninitialize.argtypes = []
ole32.CoCreateInstance.argtypes = [ctypes.POINTER(GUID), ctypes.c_void_p, wintypes.DWORD,
                                  ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p)]
ole32.CoCreateInstance.restype = HRESULT

ENUMERATOR_CLASS = "BCDE0395-E52F-467C-8E3D-C4579291692E"
ENUMERATOR_INTERFACE = "A95664D2-9614-4F35-A746-DE8DB63617E6"
MANAGER_INTERFACE = "77AA99A0-1BD6-484F-8BC7-2C654C9A9B6F"
CONTROL_INTERFACE = "BFB7FF88-7239-4FC9-8FA2-07C950BE9C6D"
VOLUME_INTERFACE = "87CE5498-68D6-44E5-9215-6DA47EF883D8"


def validate_volume(value) -> float:
    if isinstance(value, bool):
        raise ValueError("Volume must be a finite number from 0 to 1.")
    try:
        volume = float(value)
    except (ValueError, TypeError) as ex:
        raise ValueError("Volume must be a finite number from 0 to 1.") from ex
    if not math.isfinite(volume) or not 0 <= volume <= 1:
        raise ValueError("Volume must be a finite number from 0 to 1.")
    return volume


def _guid(text):
    return GUID.from_buffer_copy(uuid.UUID(text).bytes_le)


def _call(pointer, slot, *types):
    vtable = ctypes.cast(pointer, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    return ctypes.WINFUNCTYPE(HRESULT, ctypes.c_void_p, *types)(vtable[slot])


def _check(result, operation):
    if result < 0:
        raise OSError("%s failed (HRESULT 0x%08X)" % (operation, result & 0xFFFFFFFF))


@contextmanager
def _owned(pointer):
    try:
        yield pointer
    finally:
        if pointer:
            _call(pointer, 2)(pointer)


def _interface(pointer, iid):
    result = ctypes.c_void_p()
    _check(_call(pointer, 0, ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p))(
        pointer, ctypes.byref(_guid(iid)), ctypes.byref(result)), "QueryInterface")
    return result


def process_volumes(pid: int, value: float | None = None) -> list[float]:
    """Set/read every render session belonging to this PID, across active outputs."""
    if type(pid) is not int or pid <= 0:
        raise ValueError("Invalid wrapper process ID.")
    if value is not None:
        value = validate_volume(value)
    # Qt may already have initialized this thread in a different apartment.
    initialized = False
    try:
        result = ole32.CoInitializeEx(None, 2)
        initialized = result in (0, 1)
    except OSError as ex:
        if (ex.winerror or 0) & 0xFFFFFFFF != 0x80010106:
            raise
    volumes = []
    try:
        enumerator = ctypes.c_void_p()
        _check(ole32.CoCreateInstance(ctypes.byref(_guid(ENUMERATOR_CLASS)), None, 1,
                                     ctypes.byref(_guid(ENUMERATOR_INTERFACE)), ctypes.byref(enumerator)),
               "Create audio device enumerator")
        with _owned(enumerator):
            devices = ctypes.c_void_p()
            _check(_call(enumerator, 3, ctypes.c_int, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p))(
                enumerator, 0, 1, ctypes.byref(devices)), "Enumerate audio outputs")
            with _owned(devices):
                count = wintypes.UINT()
                _check(_call(devices, 3, ctypes.POINTER(wintypes.UINT))(devices, ctypes.byref(count)), "Count audio outputs")
                for index in range(count.value):
                    device = ctypes.c_void_p()
                    _check(_call(devices, 4, wintypes.UINT, ctypes.POINTER(ctypes.c_void_p))(
                        devices, index, ctypes.byref(device)), "Get audio output")
                    with _owned(device):
                        manager = ctypes.c_void_p()
                        _check(_call(device, 3, ctypes.POINTER(GUID), wintypes.DWORD, ctypes.c_void_p,
                                     ctypes.POINTER(ctypes.c_void_p))(
                            device, ctypes.byref(_guid(MANAGER_INTERFACE)), 23, None, ctypes.byref(manager)),
                               "Activate session manager")
                        with _owned(manager):
                            sessions = ctypes.c_void_p()
                            _check(_call(manager, 5, ctypes.POINTER(ctypes.c_void_p))(
                                manager, ctypes.byref(sessions)), "Enumerate audio sessions")
                            with _owned(sessions):
                                session_count = ctypes.c_int()
                                _check(_call(sessions, 3, ctypes.POINTER(ctypes.c_int))(
                                    sessions, ctypes.byref(session_count)), "Count audio sessions")
                                for session_index in range(session_count.value):
                                    control = ctypes.c_void_p()
                                    _check(_call(sessions, 4, ctypes.c_int, ctypes.POINTER(ctypes.c_void_p))(
                                        sessions, session_index, ctypes.byref(control)), "Get audio session")
                                    with _owned(control), _owned(_interface(control, CONTROL_INTERFACE)) as control2:
                                        owner = wintypes.DWORD()
                                        _check(_call(control2, 14, ctypes.POINTER(wintypes.DWORD))(
                                            control2, ctypes.byref(owner)), "Get audio session process")
                                        if owner.value != pid:
                                            continue
                                        with _owned(_interface(control, VOLUME_INTERFACE)) as volume:
                                            if value is not None:
                                                _check(_call(volume, 3, ctypes.c_float, ctypes.c_void_p)(
                                                    volume, value, None), "Set wrapper volume")
                                            current = ctypes.c_float()
                                            _check(_call(volume, 4, ctypes.POINTER(ctypes.c_float))(
                                                volume, ctypes.byref(current)), "Read wrapper volume")
                                            volumes.append(current.value)
        return volumes
    finally:
        if initialized:
            ole32.CoUninitialize()
