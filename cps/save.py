"""Ultimate-save generator for the Azimuth PC build of Forza Horizon 2.

File formats follow qiangqiang101/AnthemSaveEditor (https://github.com/qiangqiang101/AnthemSaveEditor):
  PlayerDatabase = 8 byte header + SQLite 3 database, padded to the original size
  ProfileData    = 'ssmc' + u32 length + typed value tree + opaque tail, padded to the original size
  commit         = SHA-256 manifest of the files in 'data'
"""
import hashlib
import io
import os
import shutil
import sqlite3
import struct
import tempfile
import uuid

from . import config

SAVE_FILES = ("BinarySaveState", "PlayerDatabase", "ProfileData", "SaveState", "VersionFlags")
COMMIT_HEADER = "Azimuth complete save revision v2"
TITLE_DIR = os.path.join("data", "titles", "1A96545B", "saves")
GARAGE_CAP = 250
USER_DIR = "User_9000000000000"
PROFILE_MARKER = b"\x20\x00\x00\x00\x00"

PARENT_CHILDREN = {
    ("Engine", "EngineID"): ["Camshaft", "Valves", "Displacement", "PistonsCompression", "FuelSystem", "Ignition",
                             "Exhaust", "Intake", "Flywheel", "Manifold", "RestrictorPlate", "OilCooling",
                             "SingleTurbo", "TwinTurbo", "QuadTurbo", "SuperchargerCSC", "SuperchargerDSC",
                             "Intercooler"],
    ("Drivetrain", "DrivetrainID"): ["Clutch", "Transmission", "Driveline", "Differential"],
    ("CarBody", "CarBodyID"): ["FrontBumper", "RearBumper", "Hood", "SideSkirts", "TireWidthFront", "TireWidthRear",
                               "WeightReduction", "ChassisStiffness"],
    ("Motor", "MotorID"): ["MotorParts"],
}
CAR_KEYED = ["Brakes", "SpringDamper", "AntiSwayFront", "AntiSwayRear", "TireCompound", "RearWing", "RimSizeFront",
             "RimSizeRear"]
TABLE = {"Camshaft": "EngineCamshaft", "Valves": "EngineValves", "Displacement": "EngineDisplacement",
         "PistonsCompression": "EnginePistonsCompression", "FuelSystem": "EngineFuelSystem",
         "Ignition": "EngineIgnition", "Exhaust": "EngineExhaust", "Intake": "EngineIntake",
         "Flywheel": "EngineFlywheel", "Manifold": "EngineManifold", "RestrictorPlate": "EngineRestrictorPlate",
         "OilCooling": "EngineOilCooling", "SingleTurbo": "EngineTurboSingle", "TwinTurbo": "EngineTurboTwin",
         "QuadTurbo": "EngineTurboQuad", "SuperchargerCSC": "EngineCSC", "SuperchargerDSC": "EngineDSC",
         "Intercooler": "EngineIntercooler", "Clutch": "DrivetrainClutch", "Transmission": "DrivetrainTransmission",
         "Driveline": "DrivetrainDriveline", "Differential": "DrivetrainDifferential",
         "FrontBumper": "CarBodyFrontBumper", "RearBumper": "CarBodyRearBumper", "Hood": "CarBodyHood",
         "SideSkirts": "CarBodySideSkirt", "TireWidthFront": "CarBodyTireWidthFront",
         "TireWidthRear": "CarBodyTireWidthRear", "WeightReduction": "CarBodyWeight",
         "ChassisStiffness": "CarBodyChassisStiffness", "MotorParts": "MotorParts"}
# Career_Garage column <- Data_Car column
STAT_COLS = {"PerformanceIndex": "PerformanceIndex", "ClassID": "ClassID", "SpeedRating": "SpeedRating",
             "AccelerationRating": "AccelerationRating", "LaunchRating": "LaunchRating",
             "BrakingRating": "BrakingRating", "HandlingRating": "HandlingRating", "CurbWeight": "CurbWeight",
             "WeightDistribution": "WeightDistribution", "AspirationTypeId": "AspirationTypeId",
             "SimPeakPower": "SimPeakPower", "SimPeakAngVel": "SimPeakAngVel", "SimPeakTorque": "SimPeakTorque",
             "SimPeakTorqueAngVel": "SimPeakTorqueAngVel", "SimRedlineAngVel": "SimRedlineAngVel",
             "TopSpeed": "SimTopSpeed"}


def template_dir():
    return os.path.join(config.ROOT, "save_template")


# ----------------------------------------------------------------- ProfileData
class ProfileData:
    def __init__(self, raw):
        if raw[:4] != b"ssmc":
            raise ValueError("ProfileData: missing 'ssmc'")
        self.size = len(raw)
        plen = struct.unpack_from("<i", raw, 4)[0]
        f = io.BytesIO(raw[8:8 + plen])
        n = struct.unpack("<i", f.read(4))[0]
        self.roots = [self._node(f) for _ in range(n)]
        self.tail = f.read()

    @staticmethod
    def _node(f):
        nl = struct.unpack("<i", f.read(4))[0]
        name = f.read(nl).decode("ascii")
        if f.read(5) != PROFILE_MARKER:
            raise ValueError("ProfileData: bad marker at " + name)
        t = struct.unpack("<I", f.read(4))[0]
        if t == 15:
            c = struct.unpack("<i", f.read(4))[0]
            return [name, t, [ProfileData._node(f) for _ in range(c)]]
        if t in (0, 1):
            v = f.read(1)[0]
        elif t in (3, 7):
            v = struct.unpack("<i", f.read(4))[0]
        elif t == 4:
            v = struct.unpack("<Q", f.read(8))[0]
        elif t == 9:
            v = struct.unpack("<f", f.read(4))[0]
        elif t == 12:
            c = struct.unpack("<i", f.read(4))[0]
            v = f.read(c * 2).decode("utf-16-le")
        else:
            raise ValueError("ProfileData: unknown type %d at %s" % (t, name))
        return [name, t, v]

    def find(self, path):
        nodes = self.roots
        cur = None
        for part in path.strip("/").split("/"):
            cur = next((n for n in nodes if n[0] == part), None)
            if cur is None:
                return None
            nodes = cur[2] if cur[1] == 15 else []
        return cur

    def set(self, path, value):
        n = self.find(path)
        if n is None or n[1] == 15:
            return False
        n[2] = value
        return True

    def get(self, path):
        n = self.find(path)
        return None if n is None else n[2]

    @staticmethod
    def _write(out, n):
        name, t, v = n
        b = name.encode("ascii")
        out += struct.pack("<i", len(b)) + b + PROFILE_MARKER + struct.pack("<I", t)
        if t == 15:
            out += struct.pack("<i", len(v))
            for c in v:
                ProfileData._write(out, c)
        elif t in (0, 1):
            out += bytes([int(v) & 255])
        elif t in (3, 7):
            out += struct.pack("<i", int(v))
        elif t == 4:
            out += struct.pack("<Q", int(v))
        elif t == 9:
            out += struct.pack("<f", float(v))
        elif t == 12:
            out += struct.pack("<i", len(v)) + v.encode("utf-16-le")

    def serialize(self):
        body = bytearray(struct.pack("<i", len(self.roots)))
        for r in self.roots:
            self._write(body, r)
        body += self.tail
        if 8 + len(body) > self.size:
            raise ValueError("ProfileData grew beyond its original size")
        return b"ssmc" + struct.pack("<i", len(body)) + bytes(body) + b"\x00" * (self.size - 8 - len(body))


# ----------------------------------------------------------------- PlayerDatabase
def _parent_row(db, table, car_id, ref_col):
    cols = {r[1] for r in db.execute("pragma table_info(List_Upgrade%s)" % table)}
    order = "IsStock desc, Id" if "IsStock" in cols else "Id"
    return db.execute("select Id, %s from List_Upgrade%s where Id between ? and ? order by %s limit 1"
                      % (ref_col, table, order), (car_id * 1000, car_id * 1000 + 999)).fetchone()


def _stock_child(db, table, ref_col, ref):
    r = db.execute("select Id from List_Upgrade%s where %s=? and IsStock=1 order by Id limit 1" % (table, ref_col),
                   (ref,)).fetchone()
    return r[0] if r else -1


def _stock_by_car(db, table, car_id):
    r = db.execute("select Id from List_Upgrade%s where Id between ? and ? and IsStock=1 order by Id limit 1"
                   % table, (car_id * 1000, car_id * 1000 + 999)).fetchone()
    return r[0] if r else -1


def stock_parts(db, car_id, cache):
    """Stock part Id for every Career_Garage part column of a car (installed = stock rows only)."""
    parts = {}
    for (parent, ref_col), children in PARENT_CHILDREN.items():
        row = _parent_row(db, parent, car_id, ref_col)
        parts[parent] = row[0] if row else -1
        for ch in children:
            if not row:
                parts[ch] = -1
                continue
            key = (ch, row[1])
            if key not in cache:
                cache[key] = _stock_child(db, TABLE[ch], ref_col, row[1])
            parts[ch] = cache[key]
    for col in CAR_KEYED:
        parts[col] = _stock_by_car(db, col, car_id)
    return parts


def garage_rows(game_db, entries, template_row, columns, owner):
    """entries: [(garage car id, source Data_Car id)]; they differ for pack cars cloned from a donor."""
    cache = {}
    out = []
    for i, (cid, src) in enumerate(entries, 1):
        car = game_db.execute("select * from Data_Car where Id=?", (src,)).fetchone()
        if car is None:
            continue
        cd = dict(car)
        row = dict(template_row)
        row.update(Id=i, CarId=cid, PartsValue=0, DistanceDriven=0, TimeDriven=0, TotalWinnings=0, TotalRepairs=0,
                   NumVictories=0, NumPodiums=0, NumRaces=0, NumOwners=1, NumTimesSold=0, TimeDrivenInRoadTrips=0,
                   CurOwnerNumRaces=0, CurOwnerWinnings=0, NumSkillPointsEarned=0, OriginalOwner=owner,
                   CarGroup=None, Flags=0, TireBrand=None, WheelStyle=-1, DefaultManufacturerColorIndex=0,
                   LiveryFileName="", TuneFileName="", VersionedTuneId=-1, VersionedLiveryId=-1,
                   Guid=str(uuid.uuid4()), SharedID=0,
                   Thumbnail="game:\\Media\\Stripped\\RC0\\Thumbnail_%d.png" % cid)
        for gc, dc in STAT_COLS.items():
            if cd.get(dc) is not None:
                row[gc] = cd[dc]
        parts = stock_parts(game_db, src, cache)
        if cid != src:   # cloned upgrade parent rows use Ids cid*1000+n (see pack.generate_sql)
            for k in [p for (p, _r) in PARENT_CHILDREN] + CAR_KEYED:
                v = parts[k]
                if src * 1000 <= v < src * 1000 + 1000:
                    parts[k] = cid * 1000 + (v - src * 1000)
        row.update(parts)
        out.append([row.get(c) for c in columns])
    return out


def build_player_database(raw, game_db_path, owner, car_ids=None, extra_cars=(), last_car=None):
    if raw[8:23] != b"SQLite format 3":
        raise ValueError("PlayerDatabase: no SQLite signature")
    capacity = len(raw) - 8
    tmp = tempfile.mkdtemp(prefix="cps_pdb")
    path = os.path.join(tmp, "p.sqlite")
    try:
        with open(path, "wb") as f:
            f.write(raw[8:])
        pdb = sqlite3.connect(path)
        pdb.execute("pragma journal_mode=DELETE")
        pdb.row_factory = sqlite3.Row
        columns = [r[1] for r in pdb.execute("pragma table_info(Career_Garage)")]
        templ = pdb.execute("select * from Career_Garage where TuneFileName='' order by Id limit 1").fetchone() \
            or pdb.execute("select * from Career_Garage limit 1").fetchone()
        template_row = dict(zip(columns, tuple(templ)))
        game = sqlite3.connect("file:%s?mode=ro" % game_db_path.replace("\\", "/"), uri=True)
        game.row_factory = sqlite3.Row
        if car_ids is None:
            car_ids = [r[0] for r in game.execute("select Id from Data_Car order by Id")]
        # The game discards the whole garage at startup when it holds too many cars (250 ok, 300 wiped),
        # so pack cars and the car under test come first and the rest fills up to the cap.
        pack_ids = {c for c, _ in extra_cars}
        car_ids = [c for c in car_ids if c not in pack_ids]
        room = max(0, GARAGE_CAP - len(pack_ids))
        keep = car_ids[:room]
        if last_car in car_ids and last_car not in keep:
            keep = keep[:-1] + [last_car] if keep else [last_car]
        entries = [(c, d) for c, d in extra_cars] + [(c, c) for c in keep]
        rows = garage_rows(game, entries, template_row, columns, owner)
        pdb.execute("delete from Career_Garage")
        pdb.executemany("insert into Career_Garage (%s) values (%s)" % (
            ",".join('"%s"' % c for c in columns), ",".join("?" * len(columns))), rows)
        pdb.execute("delete from LastUsedGarageId")
        id_col, car_col = columns.index("Id"), columns.index("CarId")
        last = next((r[id_col] for r in rows if r[car_col] == last_car), rows[0][id_col])
        pdb.execute("insert into LastUsedGarageId values (?)", (last,))
        pdb.commit()
        pdb.execute("vacuum")
        pdb.close()
        body = open(path, "rb").read()
        if len(body) > capacity:
            raise ValueError("PlayerDatabase grew beyond its original size (%d > %d)" % (len(body), capacity))
        return raw[:8] + body + b"\x00" * (capacity - len(body)), len(rows)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ----------------------------------------------------------------- revision
def write_commit(rev_dir):
    data = os.path.join(rev_dir, "data")
    names = sorted(n for n in os.listdir(data))
    lines = [COMMIT_HEADER, "blobs %d" % len(names)]
    for n in names:
        b = open(os.path.join(data, n), "rb").read()
        lines.append("%s %d %s" % (hashlib.sha256(b).hexdigest(), len(b), n))
    with open(os.path.join(rev_dir, "commit"), "wb") as f:
        f.write(("\n".join(lines) + "\n").encode("utf-8"))


def build_ultimate_save(mount, dest_saves_root, player_dir="player-1", credits=999999999, extra_cars=(),
                        owner="CarPackStudio", last_car=None, log=print):
    """Writes a fresh revision (a complete save) into <dest_saves_root>\\<player_dir>\\User_...\\revisions.
    extra_cars: [(new car id, donor car id)] for pack cars."""
    tpl = os.path.join(template_dir(), "data")
    if not os.path.isdir(tpl):
        raise FileNotFoundError("save template missing: " + tpl)
    gamedb = os.path.join(mount, "media", "stripped", "gamedbRC.slt")
    user = os.path.join(dest_saves_root, player_dir, USER_DIR)
    shutil.rmtree(user, ignore_errors=True)
    rev = os.path.join(user, "revisions", "%016d" % 1)
    data = os.path.join(rev, "data")
    os.makedirs(data)
    for n in SAVE_FILES:
        shutil.copyfile(os.path.join(tpl, n), os.path.join(data, n))
    prof = ProfileData(open(os.path.join(data, "ProfileData"), "rb").read())
    prof.set("/Main/Credits", int(credits))
    with open(os.path.join(data, "ProfileData"), "wb") as f:
        f.write(prof.serialize())
    pdb, n = build_player_database(open(os.path.join(data, "PlayerDatabase"), "rb").read(), gamedb, owner,
                                   extra_cars=extra_cars, last_car=last_car)
    with open(os.path.join(data, "PlayerDatabase"), "wb") as f:
        f.write(pdb)
    write_commit(rev)
    log("save: %d cars in garage, %s credits" % (n, "{:,}".format(credits)))
    return rev
