"""Forza 'burG' bundle container (.modelbin, .swatchbin, ...). Layout documented in FM5import\\ForzaModel.cs."""
import struct


class Blob:
    __slots__ = ("tag", "major", "minor", "meta_count", "meta_offset", "data", "meta", "id")

    def __repr__(self):
        return "<Blob %s v%d.%d %d bytes id=%s>" % (self.tag, self.major, self.minor, len(self.data), self.id)


class Bundle:
    def __init__(self, data: bytes):
        if data[:4] != b"burG":
            raise ValueError("not a burG bundle")
        self.version, n, self.data_start, self.total = struct.unpack_from("<HHII", data, 4)
        self.blobs = []
        for i in range(n):
            o = 16 + 24 * i
            b = Blob()
            b.tag = data[o:o + 4][::-1].decode("latin1")
            b.major, b.minor, b.meta_count, b.meta_offset, off, size, _ = struct.unpack_from("<BBHIIII", data, o + 4)
            b.data = data[off:off + size]
            b.meta = {}
            b.id = None
            for m in range(b.meta_count):
                me = b.meta_offset + 8 * m
                mt = data[me:me + 4][::-1].decode("latin1")
                ln, po = struct.unpack_from("<HH", data, me + 4)
                ln >>= 4
                b.meta[mt] = data[me + po:me + po + ln]
                if mt == "Id  " and ln == 4:
                    b.id = struct.unpack_from("<i", data, me + po)[0]
            self.blobs.append(b)

    def all(self, tag):
        return [b for b in self.blobs if b.tag == tag]

    def summary(self):
        return [(b.tag, "%d.%d" % (b.major, b.minor), len(b.data), sorted(b.meta)) for b in self.blobs]
