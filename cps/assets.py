"""Read-only asset inspection; unknown fields remain explicitly undecoded."""
import json
import re
import struct
import uuid

from .bundle import Bundle

TEXT_EXT = (".xml", ".xaml", ".txt", ".log", ".ini", ".sql", ".json", ".avpins")
BINARY_EXT = (".materialbin", ".shaderbin", ".pso", ".vso", ".carbin", ".bin")
SUPPORTED_EXT = TEXT_EXT + BINARY_EXT + (".swatchbin", ".modelbin", ".bank")
FILE_FILTER = "Supported assets (%s);;ZIP archives (*.zip);;All files (*)" % " ".join(
    "*" + ext for ext in SUPPORTED_EXT)


class Reader:
    def __init__(self, data):
        self.data = data
        self.pos = 0

    def take(self, size):
        if size < 0 or self.pos + size > len(self.data):
            raise ValueError("Truncated asset at byte 0x%X (need %d bytes)" % (self.pos, size))
        value = self.data[self.pos:self.pos + size]
        self.pos += size
        return value

    def unpack(self, fmt):
        return struct.unpack("<" + fmt, self.take(struct.calcsize("<" + fmt)))

    def u32(self):
        return self.unpack("I")[0]

    def string(self):
        return self.take(self.u32()).decode("utf-8", "replace")


def strings(data, limit=5000):
    rows = []
    for pattern, encoding in ((rb"[\x20-\x7e]{4,}", "ascii"),
                              (rb"(?:[\x20-\x7e]\x00){4,}", "utf-16-le")):
        for match in re.finditer(pattern, data):
            rows.append((match.start(), match.group().decode(encoding)))
            if len(rows) >= limit:
                return sorted(rows), True
    return sorted(rows), False


def hex_page(data, offset=0, size=65536):
    rows = []
    for pos in range(offset, min(len(data), offset + size), 16):
        part = data[pos:pos + 16]
        rows.append("%08X  %-47s  %s" % (pos, part.hex(" ").upper(),
                                        "".join(chr(b) if 32 <= b < 127 else "." for b in part)))
    return "\n".join(rows)


def _lights(data):
    r = Reader(data)
    first = r.u32()
    version = r.u32() if first == 0xDEADBEEF else 0
    if version not in (0, 1, 2, 3):
        raise ValueError("Unsupported lights.bin version %d" % version)
    count = r.u32() if first == 0xDEADBEEF else first
    if count > len(data) // 6:
        raise ValueError("Invalid light attachment count")
    attachments = [{"path": r.string(), "bone_index": r.unpack("H")[0]} for _ in range(count)]
    count = r.u32()
    if count > (len(data) - r.pos) // 76:
        raise ValueError("Invalid light count")
    lights = []
    for _ in range(count):
        light_id, flags, attachment = r.unpack("III")
        entry = {"id": "0x%08X" % light_id, "flags": "0x%08X" % flags, "attachment": attachment}
        for name in ("position", "rotation", "damage_position", "damage_rotation"):
            entry[name] = r.unpack("4f")
        if version >= 2:
            entry["preset"] = r.string()
        if version >= 3:
            entry["guid"] = str(uuid.UUID(bytes_le=r.take(16)))
        lights.append(entry)
    return {"format": "Car lights", "version": version, "attachments": attachments, "lights": lights,
            "remaining_bytes": len(data) - r.pos,
            "note": "LOD overrides, if present, remain available in the Bytes tab."}


def _car_scene(data):
    r = Reader(data)
    version = r.unpack("H")[0]
    if version != 5:
        return {"format": "Car scene", "scene_version": version,
                "note": "Only the FH2 version-5 header is decoded. Use Strings and Bytes for this version."}
    build_guid = str(uuid.UUID(bytes_le=r.take(16)))
    strict = r.take(1)[0]
    ordinal = r.u32()
    name, skeleton = r.string(), r.string()
    lod = r.unpack("H")[0]
    return {"format": "FH2 car scene", "scene_version": version, "build_guid": build_guid,
            "strict_build": bool(strict), "ordinal": ordinal, "scene_name": name,
            "skeleton": skeleton, "lod_flags": "0x%04X" % lod,
            "note": "Part records are not semantically decoded. Model/material references appear in Strings."}


def _dxbc(data):
    r = Reader(data)
    r.take(20)
    version, length, count = r.unpack("III")
    if length != len(data) or count > (len(data) - 32) // 4:
        raise ValueError("Invalid DXBC container length/chunk table")
    offsets = r.unpack("%dI" % count)
    chunks = []
    for offset in offsets:
        if offset < 32 + count * 4 or offset + 8 > len(data):
            raise ValueError("Invalid DXBC chunk offset")
        tag = data[offset:offset + 4].decode("ascii", "replace")
        size = struct.unpack_from("<I", data, offset + 4)[0]
        if offset + 8 + size > len(data):
            raise ValueError("Truncated DXBC chunk %s" % tag)
        entry = {"tag": tag, "offset": offset + 8, "size": size}
        if tag in ("SHDR", "SHEX") and size >= 8:
            token, tokens = struct.unpack_from("<II", data, offset + 8)
            stage = {0: "pixel", 1: "vertex", 2: "geometry", 3: "hull", 4: "domain", 5: "compute"}
            entry.update(stage=stage.get(token >> 16, "Xbox/unknown"),
                         shader_model="%d.%d" % ((token >> 4) & 15, token & 15), token_count=tokens)
        chunks.append(entry)
    return {"format": "DXBC shader bytecode", "container_version": version, "chunks": chunks,
            "note": "Container inspection, not shader decompilation. Xbox-specific instructions remain bytecode."}


def inspect_binary(name, data):
    low = name.replace("\\", "/").rsplit("/", 1)[-1].lower()
    if data.startswith(b"burG"):
        bundle = Bundle(data)
        result = {"format": "Forza burG bundle", "version": bundle.version,
                  "blobs": [{"tag": b.tag, "version": "%d.%d" % (b.major, b.minor), "id": b.id,
                             "size": len(b.data), "metadata": {k: v.hex(" ") for k, v in b.meta.items()}}
                            for b in bundle.blobs],
                  "note": "Bundle structure and metadata decoded; format-specific payloads are not edited."}
    elif data.startswith(b"DXBC"):
        result = _dxbc(data)
    elif low == "lights.bin":
        result = _lights(data)
    elif low.endswith(".carbin"):
        result = _car_scene(data)
    elif data.startswith(b"LDFB"):
        r = Reader(data)
        r.take(4)
        result = {"format": "LDFB binary XML", "header_words": r.unpack("4I"),
                  "note": "Binary XML payload is not decoded into editable XML. See Strings and Bytes."}
    else:
        result = {"format": "Physics definition" if low == "physicsdefinition.bin" else "Binary asset",
                  "header_u32_le": [struct.unpack_from("<I", data, i)[0]
                                    for i in range(0, min(len(data) - len(data) % 4, 64), 4)],
                  "note": "No verified semantic decoder for this layout. Raw values, strings and all bytes "
                          "are available; no collision shapes or field meanings are inferred."}
    result["file"] = name
    result["bytes"] = len(data)
    return json.dumps(result, indent=2)
