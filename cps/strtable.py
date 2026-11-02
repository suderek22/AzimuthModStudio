"""Reader for the game's LSB2 string tables (media\\Stripped\\StringTables\\<lang>.zip\\*.str)."""
import struct
import zipfile
import zlib


class StrTable:
    def __init__(self, data: bytes):
        if data[:4] != b"LSB2":
            raise ValueError("not an LSB2 string table")
        count = struct.unpack_from("<I", data, 0x10)[0]
        offs = struct.unpack_from("<%dI" % count, data, 0x14)
        self.strings = {}   # key -> text
        self.names = {}     # key -> IDS_ name
        s0 = offs[0]
        n = struct.unpack_from("<I", data, s0 + 4)[0]
        ent = [struct.unpack_from("<HI", data, s0 + 8 + 6 * i) for i in range(n + 1)]
        base = s0 + 8 + 6 * (n + 1)
        for key, off in ent[:n]:
            p = base + off * 2
            e = p
            while data[e:e + 2] != b"\0\0":
                e += 2
            self.strings[key] = data[p:e].decode("utf-16le", "replace")
        if count > 1 and offs[1]:
            s1 = offs[1]
            n1 = struct.unpack_from("<I", data, s1 + 4)[0]
            nb = s1 + 8 + 8 * n1
            for i in range(n1):
                key, _idx, off = struct.unpack_from("<HHI", data, s1 + 8 + 8 * i)
                p = nb + off
                e = data.index(b"\0", p)
                self.names[key] = data[p:e].decode("ascii", "replace")


def open_tables(mount: str, lang: str = "EN"):
    return zipfile.ZipFile(mount + "\\media\\Stripped\\StringTables\\" + lang + ".zip")


def crc32_name(name: str) -> int:
    return zlib.crc32(name.encode("ascii")) & 0xFFFFFFFF
