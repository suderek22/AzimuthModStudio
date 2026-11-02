import json
import os
import sys

APPDATA = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "CarPackStudio")
SETTINGS = os.path.join(APPDATA, "settings.json")
ROOT = getattr(sys, "_MEIPASS", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DEFAULTS = {
    "mount": "",
    "runtime_mode": "sandbox",          # sandbox | existing
    "azimuth_dir": "",                  # used when runtime_mode == existing
    "sandbox_dir": os.path.join(os.environ.get("LOCALAPPDATA", APPDATA), "CarPackStudio", "runtime"),
    "language": "EN",
    "last_project": "",
    "telemetry_port": 8080,
    "crash_recover": True,
    "boot_assist": True,
    "ultimate_save": True,              # sandbox only: generated save with every car and max credits
    "spawn_festival": True,             # sandbox only: Extra Options SpawnAtFestival instead of the car meet
    "experimental_live_reload": True,   # sandbox console: car <id>, once in free roam
    "save_credits": 999999999,
    "debug_map": "",
}


def load():
    d = dict(DEFAULTS)
    try:
        with open(SETTINGS, encoding="utf-8") as f:
            d.update(json.load(f))
    except (OSError, ValueError):
        pass
    return d


def save(d):
    os.makedirs(APPDATA, exist_ok=True)
    with open(SETTINGS, "w", encoding="utf-8") as f:
        json.dump(d, f, indent=2)


def runtime_src():
    return os.path.join(ROOT, "runtime")
