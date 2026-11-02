"""Swatchbin (Durango-tiled texture) reader. Detiling is done through xg.dll."""
import ctypes
import os
import struct
from ctypes import wintypes

import numpy as np

from .bundle import Bundle

# DXGI format -> (block bytes, block dim, name)
FORMATS = {
    98: (16, 4, "BC7"), 71: (8, 4, "BC1"), 74: (16, 4, "BC2"), 77: (16, 4, "BC3"),
    80: (8, 4, "BC4"), 83: (16, 4, "BC5"), 95: (16, 4, "BC6H"), 28: (4, 1, "RGBA8"),
    61: (1, 1, "R8"),
}
TILE_MODES = (13, 14, 0, 4, 8, 9, 10, 11, 12)

_xg = None
_xg_dir = None


def set_xg_dir(path):
    global _xg_dir
    _xg_dir = path


def _load_xg():
    global _xg
    if _xg:
        return _xg
    cands = [p for p in (_xg_dir, os.path.join(os.path.dirname(__file__), "..", "bin")) if p]
    for d in cands:
        f = os.path.join(d, "xg.dll")
        if os.path.exists(f):
            ctypes.windll.kernel32.SetDllDirectoryW(os.path.abspath(d))
            _xg = ctypes.CDLL(os.path.abspath(f))
            return _xg
    raise FileNotFoundError("xg.dll not found (put it into CarPackStudio\\bin)")


class _Desc(ctypes.Structure):
    _fields_ = [(n, ctypes.c_uint32) for n in (
        "Width Height MipLevels ArraySize Format SampleCount SampleQuality Usage BindFlags "
        "CpuAccessFlags MiscFlags EsramOffsetBytes EsramUsageBytes TileMode Pitch").split()]


class _Computer:
    def __init__(self, w, h, mips, fmt, tile):
        xg = _load_xg()
        d = _Desc(w, h, mips, 1, fmt, 1, 0, 0, 0, 0, 0, 0, 0, tile, 0)
        self.ptr = ctypes.c_void_p()
        fn = xg.XGCreateTexture2DComputer
        fn.argtypes = [ctypes.POINTER(_Desc), ctypes.POINTER(ctypes.c_void_p)]
        self.hr = fn(ctypes.byref(d), ctypes.byref(self.ptr))
        if self.hr != 0 or not self.ptr.value:
            self.ptr = None
            return
        vt = ctypes.cast(ctypes.cast(self.ptr, ctypes.POINTER(ctypes.c_void_p))[0],
                         ctypes.POINTER(ctypes.c_void_p))
        P = ctypes.c_void_p
        self._release = ctypes.CFUNCTYPE(ctypes.c_uint64, P)(vt[1])
        self._size = ctypes.CFUNCTYPE(ctypes.c_uint64, P)(vt[3])
        self._from = ctypes.CFUNCTYPE(ctypes.c_int, P, P, ctypes.c_uint32, ctypes.c_uint32, P,
                                      ctypes.c_uint32, ctypes.c_uint32)(vt[9])

    @property
    def ok(self):
        return self.ptr is not None

    def size(self):
        return self._size(self.ptr)

    def detile(self, tiled, row_pitch, depth_pitch):
        out = ctypes.create_string_buffer(depth_pitch)
        src = ctypes.create_string_buffer(bytes(tiled), len(tiled))
        hr = self._from(self.ptr, ctypes.addressof(out), 0, 0, ctypes.addressof(src),
                        row_pitch, depth_pitch)
        if hr != 0:
            raise OSError("XG CopyFromSubresource failed 0x%08x" % (hr & 0xFFFFFFFF))
        return out.raw

    def close(self):
        if self.ptr:
            self._release(self.ptr)
            self.ptr = None


class Texture:
    def __init__(self, width, height, mips, fmt, linear):
        self.width, self.height, self.mips, self.dxgi = width, height, mips, fmt
        self.format = FORMATS[fmt][2]
        self.linear = linear  # mip 0, still compressed

    def rgba(self):
        w, h = self.width, self.height
        f = self.format
        if f == "RGBA8":
            return np.frombuffer(self.linear, np.uint8)[: w * h * 4].reshape(h, w, 4).copy()
        if f == "R8":
            g = np.frombuffer(self.linear, np.uint8)[: w * h].reshape(h, w)
            return np.stack([g, g, g, np.full_like(g, 255)], -1)
        import texture2ddecoder as t
        fn = {"BC1": t.decode_bc1, "BC3": t.decode_bc3, "BC4": t.decode_bc4,
              "BC5": t.decode_bc5, "BC6H": t.decode_bc6, "BC7": t.decode_bc7,
              "BC2": t.decode_bc3}[f]
        raw = fn(self.linear, w, h)  # BGRA
        a = np.frombuffer(raw, np.uint8).reshape(h, w, 4)
        return a[..., [2, 1, 0, 3]].copy()


def read_header(data):
    b = Bundle(data)
    txch = None
    payload = None
    for bl in b.blobs:
        if bl.tag == "TXCB":
            payload = bl.data
            txch = bl.meta.get("TXCH")
    if txch is None or payload is None:
        raise ValueError("not a swatchbin texture")
    w, h = struct.unpack_from("<HH", txch, 16)
    mips = txch[28]
    return w, h, mips, txch, payload


def _pitches(w, h, fmt):
    bb, bd, _ = FORMATS[fmt]
    bw, bh = (w + bd - 1) // bd, (h + bd - 1) // bd
    return bw * bb, bw * bb * bh


def _roughness(tex):
    a = tex.rgba()[..., 0].astype(np.int16)
    return float(np.abs(np.diff(a, axis=0)).mean() + np.abs(np.diff(a, axis=1)).mean())


_choice = {}


def load_texture(data):
    """Identify format and tile mode by resource size; ambiguities are resolved by
    picking the candidate that yields the smoothest image."""
    w, h, mips, txch, payload = read_header(data)
    key = (w, h, mips, len(payload), txch[31:35])
    if key in _choice:
        fmt, tile = _choice[key]
        rp, dp = _pitches(w, h, fmt)
        c = _Computer(w, h, mips, fmt, tile)
        try:
            return Texture(w, h, mips, fmt, c.detile(payload, rp, dp))
        finally:
            c.close()
    cands = []
    for fmt in (98, 71, 80, 83, 77, 28, 61, 95):
        rp, dp = _pitches(w, h, fmt)
        for tile in TILE_MODES + tuple(range(1, 32)):
            if any(c[0] == fmt and c[1] == tile for c in cands):
                continue
            c = _Computer(w, h, mips, fmt, tile)
            if not c.ok:
                continue
            try:
                if c.size() == len(payload):
                    cands.append((fmt, tile, Texture(w, h, mips, fmt, c.detile(payload, rp, dp))))
            finally:
                c.close()
    if not cands:
        raise ValueError("could not identify swatchbin format (%dx%d, %d mips, %d bytes)"
                         % (w, h, mips, len(payload)))
    # Prefer the lowest format-priority group, then the smoothest tile mode within it
    first = cands[0][0]
    group = [c for c in cands if c[0] == first]
    best = min(group, key=lambda c: _roughness(c[2])) if len(group) > 1 else group[0]
    _choice[key] = (best[0], best[1])
    return best[2]
