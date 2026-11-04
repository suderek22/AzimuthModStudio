"""Resolve user destinations from named exit locators or fixed coordinates."""
import math
import re
import xml.etree.ElementTree as ET
from pathlib import Path

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


def resolve_destination(mount: str, map_name: str, place: str) -> tuple[str, tuple[float, float, float]]:
    if map_name.casefold() != "anthem":
        if map_name.casefold() in ("theisland", "island"):
            raise ValueError("Use track TheIsland to change maps. Named tp destinations are currently "
                             "available only for Anthem; use tp status to inspect your island position.")
        raise ValueError("Unknown map '%s'; supported teleport map: Anthem" % map_name)
    key = " ".join(place.casefold().split())
    target = next(((name, locator) for name, (locator, aliases) in DESTINATIONS.items() if key in aliases), None)
    if target is None:
        raise ValueError("Unknown Anthem destination '%s'. Places: %s" % (place, ", ".join(DESTINATIONS)))
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
