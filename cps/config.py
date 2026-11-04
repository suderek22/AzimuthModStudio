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
    "dependency_source": "",            # user's installed Azimuth + dependencies, never bundled
    "launch_arguments": "--game=fh2 --skip-launcher",
    "sandbox_dir": os.path.join(os.environ.get("LOCALAPPDATA", APPDATA), "CarPackStudio", "runtime"),
    "language": "EN",
    "last_project": "",
    "telemetry_port": 8080,
    "crash_recover": True,
    "boot_assist": True,
    "ultimate_save": True,              # legacy setting; sandbox always regenerates its template save
    "spawn_festival": True,             # sandbox only: Extra Options SpawnAtFestival instead of the car meet
    "experimental_live_reload": True,   # legacy setting; sandbox command hooks remain enabled
    "travel_test_binds": True,          # reserve physical radio buttons, separate from console travel
    "save_credits": 999999999,
    "debug_map": "",
    "check_updates": False,             # opt-in: contact GitHub once at startup
    "wrapper_volume": 1.0,              # process-scoped Core Audio session volume, 0..1
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
