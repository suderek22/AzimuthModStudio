"""Car pack project model, builder and installer."""
import json
import os
import re
import shutil
import sqlite3
import zipfile
from dataclasses import dataclass, field, asdict

from .zipnative import build_zip, manifest_line, merge_manifest

DATA_CAR_HASH = 0xCD49
MAKE_HASH = 0x3323
KEYED_TABLES = ("Data_Car_Buckets", "CameraOverrides", "CarExceptions")


@dataclass
class PackCar:
    media: str                 # new MediaName (zip name)
    donor: str                 # base car MediaName this is cloned from
    car_id: int = 0
    year: int = 2020
    make_id: int = 0           # existing List_CarMake.ID (0 = donor's make)
    make_name: str = ""        # used when new_make
    new_make: bool = False
    name: str = "New Car"
    short: str = ""
    thumbnail: str = ""
    sql_overrides: dict = field(default_factory=dict)   # Data_Car column -> value
    extra_sql: str = ""
    source_zip: str = ""       # project-relative imported archive; empty for donor clones

    def folder(self, project_dir):
        return os.path.join(project_dir, "cars", self.media)


@dataclass
class Project:
    name: str = "MyCarPack"
    version: str = "1.0.0"
    author: str = ""
    description: str = ""
    cars: list = field(default_factory=list)
    path: str = ""

    @property
    def dir(self):
        return os.path.dirname(self.path)

    def save(self, path=None):
        self.path = path or self.path
        os.makedirs(self.dir, exist_ok=True)
        d = asdict(self)
        d.pop("path")
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(d, f, indent=2)

    @staticmethod
    def load(path):
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        p = Project(d.get("name", "Pack"), d.get("version", "1.0.0"), d.get("author", ""),
                    d.get("description", ""), [], path)
        p.cars = [PackCar(**c) for c in d.get("cars", [])]
        return p


def q(s):
    return "'" + str(s).replace("'", "''") + "'"


def car_archive_path(project, car, donor_path):
    return os.path.join(project.dir, car.source_zip) if car.source_zip else donor_path


def car_source_media(car):
    return os.path.splitext(os.path.basename(car.source_zip))[0] if car.source_zip else car.donor


def import_car_archive(project, car, path):
    """Keep the original ZIP in the project; the selected donor supplies database rows only."""
    source = os.path.splitext(os.path.basename(path))[0]
    if not re.fullmatch(r"[A-Za-z0-9_-]+", car.media) or len(car.media) != len(source):
        raise ValueError("Media name must use letters, digits, _ or - and have %d characters." % len(source))
    if any(c.media.casefold() == car.media.casefold() or c.car_id == car.car_id for c in project.cars):
        raise ValueError("A car with this media name or ID already exists in the pack.")
    with zipfile.ZipFile(path) as archive:
        names = [n for n in archive.namelist() if not n.endswith("/")]
        if not any(n.lower().endswith(".modelbin") for n in names) or not any(
                "/" not in n and "\\" not in n and n.lower().endswith(".carbin") for n in names):
            raise ValueError("This ZIP needs a root .carbin and .modelbin assets. Select a car archive, not a mod package.")
        bad = archive.testzip()
        if bad:
            raise ValueError("Corrupt ZIP entry: %s" % bad)
    relative = os.path.join("sources", car.media, os.path.basename(path))
    target = os.path.join(project.dir, relative)
    if os.path.exists(target):
        raise ValueError("An imported source already exists at %s; choose another media name." % target)
    os.makedirs(os.path.dirname(target), exist_ok=True)
    try:
        shutil.copy2(path, target)
    except OSError:
        if os.path.isfile(target):
            os.remove(target)
        raise
    car.source_zip = relative
    project.cars.append(car)


def string_ref(table_hash, key):
    return "_&%d" % ((table_hash << 16) | key)


def alloc_key(taken, seed, lo=0xE000, hi=0xFFEF):
    k = lo + (seed * 7919) % (hi - lo)
    while k in taken:
        k = k + 1 if k < hi else lo
    taken.add(k)
    return k


def generate_sql(project, db_path):
    """Returns (sql_text, string_lines). Cars are cloned from their donor inside the game DB."""
    db = sqlite3.connect("file:%s?mode=ro" % db_path.replace("\\", "/"), uri=True)
    car_cols = [r[1] for r in db.execute("pragma table_info(Data_Car)")]
    upgrade = []
    for (t,) in db.execute("select name from sqlite_master where type='table' "
                           "and name like 'List\\_Upgrade%' escape '\\'"):
        cols = [r[1] for r in db.execute('pragma table_info("%s")' % t)]
        if "Ordinal" in cols:
            upgrade.append((t, cols))
    keyed = []
    for t in KEYED_TABLES:
        cols = [r[1] for r in db.execute('pragma table_info("%s")' % t)]
        for c in cols:
            if c.lower() == "carid":
                keyed.append((t, cols, c))
    sql, strings = [], []
    taken_car, taken_make = set(), set()
    for c in project.cars:
        row = db.execute("select Id, MakeID from Data_Car where MediaName=? collate nocase", (c.donor,)).fetchone()
        if not row:
            raise ValueError("donor car %s not found in game database" % c.donor)
        donor_id, donor_make = row
        make_id = c.make_id or donor_make
        k_name = alloc_key(taken_car, c.car_id)
        k_short = alloc_key(taken_car, c.car_id + 1)
        sql.append("-- %s (%s) cloned from %s" % (c.name, c.media, c.donor))
        sets = {"Id": str(c.car_id), "Year": str(c.year), "MakeID": str(make_id),
                "DisplayName": q(string_ref(DATA_CAR_HASH, k_name)),
                "ModelShort": q(string_ref(DATA_CAR_HASH, k_short)),
                "MediaName": q(c.media), "ContentId": "0", "IsPurchased": "0"}
        strings.append("Data_Car.str|%d|%s" % (k_name, c.name))
        strings.append("Data_Car.str|%d|%s" % (k_short, c.short or c.name))
        if c.new_make and c.make_name:
            nm_id = 9000 + c.car_id % 1000
            k_make = alloc_key(taken_make, nm_id)
            mcols = [r[1] for r in db.execute("pragma table_info(List_CarMake)")]
            ex = {"ID": str(nm_id), "DisplayName": q(string_ref(MAKE_HASH, k_make))}
            sel = ", ".join(ex.get(m, '"%s"' % m) for m in mcols)
            sql.append('INSERT OR REPLACE INTO List_CarMake (%s) SELECT %s FROM List_CarMake WHERE ID=%d;'
                       % (", ".join('"%s"' % m for m in mcols), sel, donor_make))
            strings.append("List_CarMake.str|%d|%s" % (k_make, c.make_name))
            sets["MakeID"] = str(nm_id)
            make_id = nm_id
        if make_id != donor_make:
            sets["MakeName"] = "(SELECT DisplayName FROM List_CarMake WHERE ID=%d)" % make_id
        if c.thumbnail:
            sets["Thumbnail"] = q(c.thumbnail)
        for k, v in c.sql_overrides.items():
            if k in car_cols:
                sets[k] = v if isinstance(v, str) and v.startswith("(") else q(v) if isinstance(v, str) else str(v)
        sel = ", ".join(sets.get(col, '"%s"' % col) for col in car_cols)
        sql.append('DELETE FROM Data_Car WHERE Id=%d;' % c.car_id)
        sql.append('INSERT INTO Data_Car (%s) SELECT %s FROM Data_Car WHERE Id=%d;'
                   % (", ".join('"%s"' % x for x in car_cols), sel, donor_id))
        for t, cols in upgrade:
            sql.append('DELETE FROM "%s" WHERE Ordinal=%d;' % (t, c.car_id))
            lo = donor_id * 1000
            # Deterministic Ids (car*1000+n) so a save file can reference the cloned parts
            idx = ("CASE WHEN Id BETWEEN %d AND %d THEN %d + (Id - %d) END"
                   % (lo, lo + 999, c.car_id * 1000, lo))
            sel = ", ".join(idx if x == "Id" else str(c.car_id) if x == "Ordinal" else '"%s"' % x for x in cols)
            sql.append('INSERT INTO "%s" (%s) SELECT %s FROM "%s" WHERE Ordinal=%d;'
                       % (t, ", ".join('"%s"' % x for x in cols), sel, t, donor_id))
        for t, cols, kc in keyed:
            sql.append('DELETE FROM "%s" WHERE "%s"=%d;' % (t, kc, c.car_id))
            nid = [x for x in cols if x != "Id"]
            sel = ", ".join(str(c.car_id) if x == kc else '"%s"' % x for x in nid)
            sql.append('INSERT INTO "%s" (%s) SELECT %s FROM "%s" WHERE "%s"=%d;'
                       % (t, ", ".join('"%s"' % x for x in nid), sel, t, kc, donor_id))
        if c.extra_sql.strip():
            sql.append(c.extra_sql.strip())
        sql.append("")
    db.close()
    return "\n".join(sql), strings


def build_car_zip(project, car, donor_zip_path):
    entries = {}
    source_zip = car_archive_path(project, car, donor_zip_path)
    source_media = car_source_media(car)
    with zipfile.ZipFile(source_zip) as z:
        for n in z.namelist():
            if not n.endswith("/"):
                entries[n] = z.read(n)
    folder = car.folder(project.dir)
    if os.path.isdir(folder):
        for root, _d, files in os.walk(folder):
            for f in files:
                full = os.path.join(root, f)
                rel = os.path.relpath(full, folder).replace("\\", "/")
                with open(full, "rb") as fh:
                    entries[rel] = fh.read()
    if car.media.lower() != source_media.lower():
        if len(car.media) != len(source_media):
            raise ValueError("car '%s': the media name must have exactly as many characters as the donor '%s' (%d), "
                             "because texture/model paths inside the car's binary files are rewritten in place"
                             % (car.media, source_media, len(source_media)))
        pat = re.compile(r"^%s(?=[._])" % re.escape(source_media), re.I)
        renamed = {}
        for n, b in entries.items():
            d, _, base = n.rpartition("/")
            renamed[(d + "/" if d else "") + pat.sub(car.media, base)] = _retarget(b, source_media, car.media)
        entries = renamed
    return build_zip(entries)


def _retarget(blob, donor, media):
    """Same-length, case-aware replacement of the donor name (ASCII and UTF-16) in a file."""
    for enc in ("ascii", "utf-16-le"):
        d = donor.encode(enc)
        pat = re.compile(re.escape(d), re.I)

        def sub(m, enc=enc):
            s = m.group().decode(enc)
            if s.islower():
                return media.lower().encode(enc)
            if s.isupper():
                return media.upper().encode(enc)
            return media.encode(enc)
        blob = pat.sub(sub, blob)
    return blob


def build_pack(project, mount, out_dir):
    """Writes <out_dir>\\mediapc\\cars\\*.zip and <out_dir>\\CarPacks\\<name>\\*; returns metadata."""
    media = os.path.join(mount, "media")
    sql, strings = generate_sql(project, os.path.join(media, "stripped", "gamedbRC.slt"))
    cars_dir = os.path.join(out_dir, "mediapc", "cars")
    os.makedirs(cars_dir, exist_ok=True)
    lines = {}
    for c in project.cars:
        dz = os.path.join(media, "cars", c.donor + ".zip")
        if not os.path.exists(dz):
            dz = os.path.join(mount, "mediapc", "cars", c.donor + ".zip")
        data, cs, csz, n = build_car_zip(project, c, dz)
        with open(os.path.join(cars_dir, c.media + ".zip"), "wb") as f:
            f.write(data)
        gp = "game:\\media\\cars\\%s.zip" % c.media
        lines[gp.lower()] = manifest_line(gp, cs, csz, n)
    pdir = os.path.join(out_dir, "CarPacks", project.name)
    os.makedirs(pdir, exist_ok=True)
    with open(os.path.join(pdir, "pack.sql"), "w", encoding="utf-8") as f:
        f.write(sql)
    with open(os.path.join(pdir, "strings.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(strings) + "\n")
    meta = {"name": project.name, "version": project.version, "author": project.author,
            "description": project.description, "manifest_lines": lines,
            "cars": [{"media": c.media, "id": c.car_id, "name": c.name} for c in project.cars]}
    with open(os.path.join(pdir, "pack.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)
    return meta


def _rebuild_manifest(mount, packs_dir):
    with open(os.path.join(mount, "media", "zipmanifest.xml"), "r", encoding="utf-8-sig") as f:
        text = f.read()
    lines = {}
    if os.path.isdir(packs_dir):
        for p in sorted(os.listdir(packs_dir)):
            mj = os.path.join(packs_dir, p, "pack.json")
            if os.path.exists(mj):
                with open(mj, encoding="utf-8") as f:
                    lines.update(json.load(f).get("manifest_lines", {}))
    target = os.path.join(mount, "mediapc", "zipmanifest.xml")
    if lines:
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "w", encoding="utf-8-sig", newline="") as f:
            f.write(merge_manifest(text, lines))
    elif os.path.exists(target):
        os.remove(target)


def install_pack(build_dir, mount, mods_dir):
    """Copies a built pack into Mount\\mediapc and the loader's CarPacks folder."""
    packs_dir = os.path.join(mods_dir, "CarPacks")
    name = os.listdir(os.path.join(build_dir, "CarPacks"))[0]
    shutil.copytree(os.path.join(build_dir, "CarPacks", name), os.path.join(packs_dir, name), dirs_exist_ok=True)
    dst = os.path.join(mount, "mediapc", "cars")
    os.makedirs(dst, exist_ok=True)
    for f in os.listdir(os.path.join(build_dir, "mediapc", "cars")):
        shutil.copy2(os.path.join(build_dir, "mediapc", "cars", f), dst)
    _rebuild_manifest(mount, packs_dir)
    return name


def uninstall_pack(name, mount, mods_dir):
    packs_dir = os.path.join(mods_dir, "CarPacks")
    pj = os.path.join(packs_dir, name, "pack.json")
    if os.path.exists(pj):
        with open(pj, encoding="utf-8") as f:
            for c in json.load(f).get("cars", []):
                z = os.path.join(mount, "mediapc", "cars", c["media"] + ".zip")
                if os.path.exists(z):
                    os.remove(z)
    shutil.rmtree(os.path.join(packs_dir, name), ignore_errors=True)
    _rebuild_manifest(mount, packs_dir)


def installed_packs(mods_dir):
    out = []
    pd = os.path.join(mods_dir, "CarPacks")
    if os.path.isdir(pd):
        for p in sorted(os.listdir(pd)):
            mj = os.path.join(pd, p, "pack.json")
            if os.path.exists(mj):
                with open(mj, encoding="utf-8") as f:
                    out.append(json.load(f))
    return out
