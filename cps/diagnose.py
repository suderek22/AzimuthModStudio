"""Pre-flight checks for pack cars. The game shows 'null_car_00' (grey placeholder textures) when it cannot
resolve a car's media, so every reason that can lead to it is checked and reported."""
import os
import re
import sqlite3
import zipfile

ERR, WARN, OK = "ERROR", "WARN", "ok"


def _game_cars(mount):
    db = sqlite3.connect("file:%s?mode=ro" % os.path.join(mount, "media", "stripped", "gamedbRC.slt").replace("\\", "/"),
                         uri=True)
    try:
        return {r[0]: (r[1] or "") for r in db.execute("select Id, MediaName from Data_Car")}
    finally:
        db.close()


def _has_ref(blob, name, media):
    """True when the donor name occurs outside of occurrences of the (longer) new media name."""
    low = blob.lower()
    for enc in (lambda s: s.encode("ascii", "ignore"), lambda s: s.encode("utf-16-le")):
        n, m = enc(name.lower()), enc(media.lower())
        if n in low.replace(m, b"") if m.startswith(n) else n in low:
            return True
    return False


def check_pack(project, mount, build_dir, mods_dir):
    """Returns [(level, text)]. build_dir is the output of build_pack; mods_dir the runtime's fh2-mods folder."""
    res = []
    add = lambda lv, t: res.append((lv, t))
    game = _game_cars(mount)
    media_to_id = {m.lower(): i for i, m in game.items()}
    seen_ids, seen_media = {}, {}
    manifest = ""
    mp = os.path.join(mount, "mediapc", "zipmanifest.xml")
    if os.path.exists(mp):
        manifest = open(mp, encoding="utf-8-sig", errors="replace").read().lower()
    sqlp = os.path.join(mods_dir, "CarPacks", project.name, "pack.sql")
    sql = open(sqlp, encoding="utf-8", errors="replace").read() if os.path.exists(sqlp) else None
    if sql is None:
        add(ERR, "pack.sql of '%s' is not installed in %s - the car has no database row" % (project.name, mods_dir))
    if not os.path.exists(os.path.join(mods_dir, "CarPackLoader.dll")):
        add(ERR, "CarPackLoader.dll is missing from %s - pack SQL/strings are never applied" % mods_dir)
    for c in project.cars:
        tag = "[%s]" % c.media
        before = len(res)
        if not re.fullmatch(r"[A-Za-z0-9_]+", c.media or ""):
            add(ERR, "%s media name must be letters/digits/underscore only" % tag)
        if c.car_id <= 0:
            add(ERR, "%s car id %d is invalid" % (tag, c.car_id))
        if c.car_id in game:
            add(ERR, "%s car id %d already used by game car '%s'" % (tag, c.car_id, game[c.car_id]))
        if c.car_id in seen_ids:
            add(ERR, "%s car id %d duplicates [%s]" % (tag, c.car_id, seen_ids[c.car_id]))
        seen_ids[c.car_id] = c.media
        if c.media.lower() in seen_media:
            add(ERR, "%s media name duplicates another pack car" % tag)
        seen_media[c.media.lower()] = 1
        if c.media.lower() in media_to_id and c.media.lower() != c.donor.lower():
            add(WARN, "%s media name collides with the base game car id %d" % (tag, media_to_id[c.media.lower()]))
        if c.donor and c.media.lower() != c.donor.lower() and len(c.media) != len(c.donor):
            add(ERR, "%s media name has %d chars but donor '%s' has %d - texture paths inside the car cannot be "
                     "rewritten, so the game falls back to null_car_00 textures. Use a %d-character name (e.g. %s)"
                % (tag, len(c.media), c.donor, len(c.donor), len(c.donor), c.donor[:-3] + "CPS"))
        if c.donor.lower() not in media_to_id:
            add(ERR, "%s donor '%s' is not in the game database" % (tag, c.donor))
        built = os.path.join(build_dir, "mediapc", "cars", c.media + ".zip")
        inst = os.path.join(mount, "mediapc", "cars", c.media + ".zip")
        for what, p in (("built zip", built), ("installed zip", inst)):
            if not os.path.exists(p):
                add(ERR, "%s %s missing: %s" % (tag, what, p))
        if os.path.exists(built) and os.path.exists(inst) and os.path.getsize(built) != os.path.getsize(inst):
            add(ERR, "%s installed zip differs from the built one (stale or locked file)" % tag)
        gp = ("game:\\media\\cars\\%s.zip" % c.media).lower()
        if manifest and gp not in manifest:
            add(ERR, "%s %s is not in Mount\\mediapc\\zipmanifest.xml - the game will not open the zip" % (tag, gp))
        elif not manifest:
            add(ERR, "Mount\\mediapc\\zipmanifest.xml is missing")
        if sql is not None and ("'%s'" % c.media) not in sql:
            add(ERR, "%s MediaName is not present in the installed pack.sql" % tag)
        if os.path.exists(built):
            try:
                _check_zip(c, built, mount, add, tag)
            except zipfile.BadZipFile:
                add(ERR, "%s zip is corrupt" % tag)
        if len(res) == before:
            add(OK, "%s no problems found (id %d, donor %s)" % (tag, c.car_id, c.donor))
    return res


def _check_zip(c, built, mount, add, tag):
    with zipfile.ZipFile(built) as z:
        names = [n for n in z.namelist() if not n.endswith("/")]
        if not names:
            add(ERR, "%s zip is empty" % tag)
            return
        low = c.media.lower()
        dl = c.donor.lower()
        stale = [n for n in names if low != dl and n.rpartition("/")[2].lower().startswith(dl)
                 and not n.rpartition("/")[2].lower().startswith(low)]
        if stale:
            add(WARN, "%s entries still carry the donor name (game looks up '%s*'): %s"
                % (tag, c.media, ", ".join(stale[:6]) + (" ..." if len(stale) > 6 else "")))
        if not any(n.lower().endswith(".carbin") for n in names):
            add(ERR, "%s zip has no .carbin (car model)" % tag)
        if not any(n.lower().endswith((".swatchbin", ".dds")) for n in names):
            add(ERR, "%s zip has no textures (.swatchbin/.dds)" % tag)
        donor_zip = os.path.join(mount, "media", "cars", c.donor + ".zip")
        if os.path.exists(donor_zip):
            with zipfile.ZipFile(donor_zip) as dz:
                want = {re.sub(r"^%s(?=[._])" % re.escape(c.donor), c.media, n.rpartition("/")[2], flags=re.I).lower()
                        for n in dz.namelist() if not n.endswith("/")}
            have = {n.rpartition("/")[2].lower() for n in names}
            miss = sorted(want - have)
            if miss:
                add(ERR, "%s missing files present in the donor: %s" % (tag, ", ".join(miss[:8])))
        if c.media.lower() != c.donor.lower():
            refs = []
            for n in names:
                if n.lower().endswith((".xml", ".txt", ".swatchbin", ".carbin", ".modelbin", ".ini")):
                    if _has_ref(z.read(n), c.donor, c.media):
                        refs.append(n)
            if refs:
                add(WARN, "%s still references the donor name '%s' inside: %s (textures/materials may resolve to "
                          "the donor, or fail)" % (tag, c.donor, ", ".join(refs[:6])))


def scan_logs(paths, media_names):
    """Lines of the runtime logs that mention a pack car or a missing/placeholder car."""
    pat = re.compile("null_car|" + "|".join(re.escape(m) for m in media_names) if media_names else "null_car", re.I)
    out = []
    for p in paths:
        if os.path.exists(p):
            for ln in open(p, encoding="utf-8", errors="replace"):
                if pat.search(ln):
                    out.append("%s: %s" % (os.path.basename(p), ln.rstrip()))
    return out
