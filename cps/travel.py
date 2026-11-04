"""Resolve user destinations from named exit locators or fixed coordinates."""
import json
import math
import os
import re
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

from . import config

TRACKS = {"Anthem": 542, "TheIsland": 549}


def resolve_track(value: str) -> tuple[str, int]:
    aliases = {"542": "Anthem", "549": "TheIsland", "anthem": "Anthem", "mainland": "Anthem",
               "theisland": "TheIsland", "island": "TheIsland", "stormisland": "TheIsland"}
    name = aliases.get(value.casefold().replace(" ", ""))
    if name is None:
        raise ValueError("Unknown track '%s'. Available: Anthem (542), TheIsland (549)" % value)
    return name, TRACKS[name]


DESTINATIONS: dict[str, tuple[str | tuple[float, float, float], tuple[str, ...]]] = {
    "Festival": ("festival_locator_garage_exit", ("festival",)),
    "Nicea": ("destination_car_place_exit_locator_westnice", ("nicea", "nice", "westnice")),
    "Saint-Martin": ("destination_car_place_exit_locator_eastnice", ("saint-martin", "saint martin", "eastnice")),
    "Sisteron": ("destination_car_place_exit_locator_sisteron", ("sisteron",)),
    "Castelletto": ("destination_car_place_exit_locator_atrani", ("castelletto", "atrani", "antrani")),
    "Montellino": ("destination_car_place_exit_locator_montichello", ("montellino", "montichello")),
    "San Giovanni": ("destination_car_place_exit_locator_sangimignano", ("san giovanni", "sangimignano")),
    "Airport": ((425.574, 110.821, -1478.082), ("airport",)),
}

ISLAND_DESTINATIONS: dict[str, tuple[tuple[float, float, float], tuple[str, ...]]] = {
    "Festival": ((9218.77, 139.458, -2889.03), ("festival", "outpost", "hub", "garage")),
    "Car Meet": ((8837.37, 119.409, -3432.21), ("car meet", "carmeet")),
    "Docks": ((8729.18, 86.49, -1133.23), ("docks", "port", "mainland", "arrival")),
    "Barn Find": ((9934.19, 180.944, -4557.42), ("barn find", "barnfind", "barn")),
}
MAP_DESTINATIONS = {"Anthem": DESTINATIONS, "TheIsland": ISLAND_DESTINATIONS}


def resolve_destination(mount: str, map_name: str, place: str) -> tuple[str, tuple[float, float, float]]:
    map_name, _ = resolve_track(map_name)
    destinations = MAP_DESTINATIONS[map_name]
    key = " ".join(place.casefold().split())
    target = next(((name, locator) for name, (locator, aliases) in destinations.items() if key in aliases), None)
    if target is None:
        raise ValueError("Unknown %s destination '%s'. Places: %s" % (map_name, place, ", ".join(destinations)))
    name, locator = target
    if not isinstance(locator, str):
        return name, locator
    path = Path(mount) / "media" / "tracks" / "anthem" / "trackroutes" / "Route0.nt"
    data = path.read_bytes()
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = data.decode("cp1252")
    # NT files contain several top-level XML sections rather than one document element.
    text = re.sub(r"<\?xml[^>]*\?>", "", text)
    root = ET.fromstring("<route>" + text + "</route>")
    found = []
    for item in root.iter("Locator"):
        label = item.find("Name")
        transform = item.find("SceneTransform")
        if label is not None and label.get("value") == locator and transform is not None:
            values = tuple(float(transform.attrib["value._4%d" % i].rstrip("'")) for i in (1, 2, 3))
            if not all(math.isfinite(value) and abs(value) < 100000 for value in values):
                raise ValueError("Invalid coordinates for locator '%s'" % locator)
            found.append(values)
    if len(found) != 1:
        raise ValueError("Expected one locator '%s' in %s, found %d" % (locator, path, len(found)))
    return name, found[0]


@dataclass(frozen=True)
class TeleportPlace:
    name: str
    track_id: int
    position: tuple[float, float, float]


def place_key(name: str) -> str:
    return " ".join(name.casefold().split())


def validate_place_name(name: str) -> str:
    if any(ord(char) < 32 for char in name):
        raise ValueError("Place names cannot contain control characters.")
    name = " ".join(name.split())
    if not name or len(name) > 80 or '"' in name or "\\" in name:
        raise ValueError('Place name must be 1-80 characters, without quotes or backslashes.')
    if place_key(name) in ("list", "status", "add", "remove"):
        raise ValueError("That name is reserved for a tp command.")
    return name


def validate_position(values: object) -> tuple[float, float, float]:
    if not isinstance(values, (list, tuple)) or len(values) != 3:
        raise ValueError("Teleport position must contain exactly three coordinates.")
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or
           abs(v) > 100000 or not math.isfinite(v) for v in values):
        raise ValueError("Teleport coordinates must be finite numbers within +/-100000.")
    return float(values[0]), float(values[1]), float(values[2])


def parse_player_position(reply: str) -> tuple[int, tuple[float, float, float]]:
    match = re.fullmatch(r"tp track=(\d+) settled=1 queued=0 active=0 applied=[01] position=([^,]+),([^,]+),([^,]+)",
                         reply.strip())
    if not match:
        raise ValueError("Cannot capture a settled player position: " + reply)
    track_id = int(match[1])
    if track_id <= 0:
        raise ValueError("Player track ID is invalid.")
    return track_id, validate_position([float(match[i]) for i in (2, 3, 4)])


def load_places() -> list[TeleportPlace]:
    path = Path(config.APPDATA) / "teleport_places.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    if not isinstance(data, dict) or data.get("version") != 1 or not isinstance(data.get("places"), list):
        raise ValueError("Invalid custom teleport file: " + str(path))
    places = []
    keys = set()
    for entry in data["places"]:
        if not isinstance(entry, dict) or not isinstance(entry.get("name"), str):
            raise ValueError("Invalid custom teleport place in " + str(path))
        name = validate_place_name(entry["name"])
        track_id = entry.get("track_id")
        if type(track_id) is not int or track_id <= 0 or track_id > 2147483647:
            raise ValueError("Invalid custom teleport track ID in " + str(path))
        key = (track_id, place_key(name))
        if key in keys:
            raise ValueError("Duplicate custom teleport place in " + str(path))
        keys.add(key)
        places.append(TeleportPlace(name, track_id, validate_position(entry.get("position"))))
    return places


def save_places(places: list[TeleportPlace]) -> None:
    directory = Path(config.APPDATA)
    directory.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=directory,
                                         prefix="teleport_places-", suffix=".tmp", delete=False) as file:
            temporary = Path(file.name)
            json.dump({"version": 1, "places": [
                {"name": place.name, "track_id": place.track_id, "position": place.position} for place in places
            ]}, file, indent=2, allow_nan=False)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, directory / "teleport_places.json")
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def add_place(place: TeleportPlace) -> TeleportPlace:
    name = validate_place_name(place.name)
    map_name = track_label(place.track_id)
    if any(place_key(name) in aliases for _, aliases in MAP_DESTINATIONS.get(map_name, {}).values()):
        raise ValueError("That name belongs to a built-in place on %s." % map_name)
    places = load_places()
    if any(p.track_id == place.track_id and place_key(p.name) == place_key(name) for p in places):
        raise ValueError("Custom place already exists on %s; remove it first to replace it." % map_name)
    saved = TeleportPlace(name, place.track_id, validate_position(place.position))
    save_places(places + [saved])
    return saved


def remove_place(track_id: int, name: str) -> TeleportPlace:
    places = load_places()
    found = next((p for p in places if p.track_id == track_id and place_key(p.name) == place_key(name)), None)
    if found is None:
        raise ValueError('Custom place "%s" does not exist on %s; built-in places cannot be removed.' %
                         (name, track_label(track_id)))
    save_places([p for p in places if p != found])
    return found


def track_label(track_id: int) -> str:
    return next((name for name, value in TRACKS.items() if value == track_id), str(track_id))


def resolve_place_track(value: str) -> tuple[str, int]:
    if value.isascii() and value.isdigit() and 0 < int(value) <= 2147483647:
        return track_label(int(value)), int(value)
    return resolve_track(value)


def teleport_entries() -> list[str]:
    entries = ["tp %s %s" % (map_name, name) for map_name, destinations in MAP_DESTINATIONS.items()
               for name in destinations]
    entries.extend('tp %s "%s" [custom]' % (track_label(place.track_id), place.name) for place in load_places())
    return entries
