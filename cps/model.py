"""Decoder for .modelbin meshes: positions/normals/UVs + triangle lists, for the 3D viewer."""
import struct
from dataclasses import dataclass, field

import numpy as np

from .bundle import Bundle

# DXGI formats seen in Forza vertex layouts -> (bytes, kind, components)
_FMT = {
    2: (16, "f32", 4), 6: (12, "f32", 3), 16: (8, "f32", 2), 41: (4, "f32", 1),
    10: (8, "f16", 4), 11: (8, "u16n", 4), 13: (8, "s16n", 4),
    35: (4, "u16n", 2), 37: (4, "s16n", 2), 28: (4, "u8n", 4), 31: (4, "u10", 4),
    24: (4, "u10", 4), 34: (4, "f16", 2), 54: (4, "f16", 2),
}


def fmt_size(f):
    return _FMT[f][0] if f in _FMT else 0


def decode_attr(raw: np.ndarray, off: int, fmt: int, count: int, stride: int):
    """raw: uint8 array of the whole buffer. Returns float32 (count, comps) or None."""
    if fmt not in _FMT:
        return None
    size, kind, comps = _FMT[fmt]
    idx = (np.arange(count, dtype=np.int64) * stride + off)[:, None] + np.arange(size)[None, :]
    chunk = np.ascontiguousarray(raw[idx])
    if kind == "f32":
        return chunk.view("<f4").reshape(count, comps).astype(np.float32)
    if kind == "f16":
        return chunk.view("<f2").reshape(count, comps).astype(np.float32)
    if kind == "u16n":
        return chunk.view("<u2").reshape(count, comps).astype(np.float32) / 65535.0
    if kind == "s16n":
        return np.maximum(chunk.view("<i2").reshape(count, comps).astype(np.float32) / 32767.0, -1.0)
    if kind == "u8n":
        return chunk.reshape(count, comps).astype(np.float32) / 255.0
    if kind == "u10":
        v = chunk.view("<u4").reshape(count)
        return np.stack([(v & 1023), (v >> 10) & 1023, (v >> 20) & 1023, v >> 30], 1).astype(np.float32) / 1023.0
    return None


@dataclass
class Layout:
    names: list
    elems: list   # dict(sem, index, slot, fmt, off)

    @staticmethod
    def parse(d: bytes):
        p = 0
        nn = struct.unpack_from("<H", d, p)[0]
        p += 2
        names = []
        for _ in range(nn):
            ln = struct.unpack_from("<i", d, p)[0]
            p += 4
            names.append(d[p:p + ln].decode("ascii", "replace"))
            p += ln
        ne = struct.unpack_from("<H", d, p)[0]
        p += 2
        elems = []
        cursor = {}
        for _ in range(ne):
            ni, idx, slot, _pad, fmt, off, _cls = struct.unpack_from("<HHHHIII", d, p)
            if off == 0xFFFFFFFF:   # packed: elements follow each other within a slot
                off = cursor.get(slot, 0)
            cursor[slot] = off + fmt_size(fmt)
            elems.append(dict(sem=names[ni], index=idx, slot=slot, fmt=fmt, off=off))
            p += 20
        return Layout(names, elems)


@dataclass
class MeshData:
    name: str
    material: int
    pos: np.ndarray
    nrm: np.ndarray | None
    uv: np.ndarray | None
    tris: np.ndarray
    bbox: tuple = ()


@dataclass
class ModelData:
    meshes: list = field(default_factory=list)
    materials: list = field(default_factory=list)   # names
    notes: list = field(default_factory=list)


def _index_buffer(blob):
    d = blob.data
    cnt, size, stride = struct.unpack_from("<IIH", d, 0)
    dt = "<u4" if stride == 4 else "<u2"
    return np.frombuffer(d, dtype=dt, count=cnt, offset=16).astype(np.int64)


def load_model(data: bytes) -> ModelData:
    b = Bundle(data)
    out = ModelData()
    for m in b.all("MatI"):
        out.materials.append(m.meta.get("Name", b"").split(b"\0")[0].decode("latin1"))
    layouts = {l.id: Layout.parse(l.data) for l in b.all("VLay")}
    vbs = {}
    for v in b.all("VerB"):
        cnt, size, stride, _ne, _first = struct.unpack_from("<IIHHI", v.data, 0)
        vbs[v.id] = (cnt, stride, np.frombuffer(v.data, dtype=np.uint8, count=size, offset=16))
    ibs = {i.id: _index_buffer(i) for i in b.all("IndB")}
    ib0 = ibs.get(0) if ibs else None
    for blob in b.all("Mesh"):
        d = blob.data
        mat = struct.unpack_from("<H", d, 0)[0]
        if mat == 0xFFFF:
            continue
        istart = struct.unpack_from("<i", d, 0x19)[0]
        base = struct.unpack_from("<i", d, 0x1D)[0]
        icount = struct.unpack_from("<i", d, 0x21)[0]
        vcount = struct.unpack_from("<i", d, 0x2D)[0]
        lay_id, nstreams = struct.unpack_from("<ii", d, 0x31)
        lay = layouts.get(lay_id)
        if lay is None or ib0 is None:
            out.notes.append("mesh %s: missing layout/index buffer" % blob.meta.get("Name"))
            continue
        streams = []
        for s in range(min(nstreams, 2)):
            vi, slot, stride, voff = struct.unpack_from("<iiii", d, 0x39 + 16 * s)
            streams.append((vi, slot, stride, voff))
        pos = nrm = uv = None
        for e in lay.elems:
            st = next((x for x in streams if x[1] == e["slot"]), None)
            if st is None or st[0] not in vbs:
                continue
            vi, slot, stride, voff = st
            cnt, vstride, raw = vbs[vi]
            first = voff // vstride if vstride else 0
            a = decode_attr(raw, e["off"] + first * vstride, e["fmt"], vcount, vstride)
            if a is None:
                continue
            if e["sem"] == "POSITION" and e["index"] == 0:
                pos = a
            elif e["sem"] == "NORMAL" and e["index"] == 0:
                nrm = a
            elif e["sem"] == "TEXCOORD" and e["index"] == 0:
                uv = a
        if pos is None:
            out.notes.append("mesh %s: no decodable POSITION" % blob.meta.get("Name"))
            continue
        bb = struct.unpack("<6f", blob.meta["BBox"][:24]) if "BBox" in blob.meta else ()
        idx = ib0[istart:istart + icount] + base
        tris = idx[: len(idx) // 3 * 3].reshape(-1, 3)
        tris = tris[(tris >= 0).all(1) & (tris < vcount).all(1)]
        name = blob.meta.get("Name", b"").split(b"\0")[0].decode("latin1")
        out.meshes.append(MeshData(name, mat, pos[:, :3] if pos.shape[1] >= 3 else np.pad(pos, ((0, 0), (0, 3 - pos.shape[1]))),
                                   nrm[:, :3] if nrm is not None and nrm.shape[1] >= 3 else None,
                                   uv[:, :2] if uv is not None and uv.shape[1] >= 2 else None, tris, bb))
    return out
