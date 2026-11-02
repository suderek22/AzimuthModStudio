"""Writes zips in the layout the Forza/Azimuth loader expects (port of Repack-ForzaZip.ps1)
and maintains zipmanifest.xml."""
import re
import struct
import zlib


def build_zip(entries):
    """entries: ordered mapping name -> bytes. Returns (zip_bytes, cd_start, cd_size, count)."""
    body = bytearray()
    cd = bytearray()
    for name, raw in entries.items():
        nb = name.encode("utf-8")
        co = zlib.compressobj(9, zlib.DEFLATED, -15)
        comp = co.compress(raw) + co.flush()
        crc = zlib.crc32(raw) & 0xFFFFFFFF
        local = len(body)
        body += struct.pack("<IHHHHHIIIHH", 0x04034B50, 10, 0, 8, 0, 0, crc, len(comp), len(raw), len(nb), 0)
        body += nb
        data_off = len(body)
        body += comp
        cd += struct.pack("<IHHHHHHIIIHHHHHII", 0x02014B50, 10, 10, 0, 8, 0, 0, crc, len(comp), len(raw),
                          len(nb), 8, 0, 0, 0, 0, local)
        cd += nb
        cd += struct.pack("<HHI", 0x1123, 4, data_off)
    cd_start = len(body)
    out = bytes(body) + bytes(cd) + struct.pack("<IHHHHIIH", 0x06054B50, 0, 0, len(entries), len(entries),
                                                  len(cd), cd_start, 0)
    return out, cd_start, len(cd), len(entries)


def manifest_line(game_path, cd_start, cd_size, count):
    return ('  <Zip version="1" path="%s" priority="80" dirstart="%d" dirsize="%d" direntries="%d" />'
            % (game_path.lower(), cd_start, cd_size + 22, count))


def merge_manifest(base_text, lines):
    """Insert/replace Zip lines (dict game_path_lower -> line) into the manifest text."""
    text = base_text
    for gp, line in lines.items():
        pat = re.compile(r'[ \t]*<Zip [^>]*path="%s"[^>]*/>\r?\n?' % re.escape(gp), re.I)
        text = pat.sub("", text)
    add = "".join(l + "\r\n" for l in lines.values())
    return text.replace("</ZipFiles>", add + "</ZipFiles>")
