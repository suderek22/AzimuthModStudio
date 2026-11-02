"""Reads installed/base cars from a game Mount directory."""
import os
import sqlite3
import zipfile
from dataclasses import dataclass, field

from .strtable import StrTable

TABLE_HASH = {"Data_Car.str": 0xCD49, "List_CarMake.str": 0x3323}


@dataclass
class Car:
    id: int
    media: str
    year: int
    make: str
    name: str
    short: str
    make_id: int
    source: str = "base"      # base | mod
    zip_path: str = ""
    extra: dict = field(default_factory=dict)

    @property
    def title(self):
        return "%s %s %s" % (self.year, self.make, self.name)


class GameData:
    def __init__(self, mount):
        self.mount = mount
        self.media = os.path.join(mount, "media")
        self.db_path = os.path.join(self.media, "stripped", "gamedbRC.slt")
        self.cars = []

    @staticmethod
    def valid(mount):
        return os.path.isfile(os.path.join(mount, "media", "stripped", "gamedbRC.slt"))

    def _strings(self, lang="EN"):
        out = {}
        zp = os.path.join(self.media, "Stripped", "StringTables", lang + ".zip")
        if not os.path.exists(zp):
            return out
        z = zipfile.ZipFile(zp)
        for n in z.namelist():
            base = n.split("/")[-1]
            if base in TABLE_HASH:
                out[TABLE_HASH[base]] = StrTable(z.read(n)).strings
        return out

    def resolve(self, strs, ref):
        if isinstance(ref, str) and ref.startswith("_&"):
            n = int(ref[2:])
            return strs.get(n >> 16, {}).get(n & 0xFFFF, "")
        return ref or ""

    def load(self, lang="EN"):
        strs = self._strings(lang)
        db = sqlite3.connect("file:%s?mode=ro" % self.db_path.replace("\\", "/"), uri=True)
        makes = {}
        for i, dn in db.execute("select ID, DisplayName from List_CarMake"):
            makes[i] = self.resolve(strs, dn)
        self.makes = makes
        carsdir = os.path.join(self.media, "cars")
        overlay = {}
        for d in ("mediapc", "mediaoverride"):
            p = os.path.join(self.mount, d, "cars")
            if os.path.isdir(p):
                for f in os.listdir(p):
                    overlay[f.lower()] = os.path.join(p, f)
        self.cars = []
        q = ("select Id, Year, MakeID, DisplayName, ModelShort, MediaName, ClassID, "
             "PerformanceIndex, CurbWeight from Data_Car order by MakeID, Year")
        for cid, year, mk, dn, ms, media, cls, pi, wt in db.execute(q):
            zp = os.path.join(carsdir, media + ".zip")
            src = "base"
            if media.lower() + ".zip" in overlay:
                zp, src = overlay[media.lower() + ".zip"], "mod"
            elif not os.path.exists(zp):
                continue
            self.cars.append(Car(cid, media, year, makes.get(mk, "?"), self.resolve(strs, dn),
                                 self.resolve(strs, ms), mk, src, zp,
                                 {"class": cls, "pi": pi, "weight": wt}))
        db.close()
        return self.cars
