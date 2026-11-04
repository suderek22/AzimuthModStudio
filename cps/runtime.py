"""Azimuth runtime management: sandbox preparation, launching, window embedding, telemetry, console pipe."""
import ctypes
import configparser
import json
import os
import re
import shutil
import struct
import sqlite3
import subprocess
import time
import threading
import urllib.request
from ctypes import wintypes
from contextlib import closing

from . import config
from .command_pipe import request as pipe_request
from .dependencies import BUNDLED_RUNTIME, import_dependencies, require_dependencies, missing_dependencies

user32 = ctypes.windll.user32
user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
user32.SetParent.argtypes = [wintypes.HWND, wintypes.HWND]
user32.SetParent.restype = wintypes.HWND
user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
user32.GetAncestor.restype = wintypes.HWND
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.SetActiveWindow.argtypes = [wintypes.HWND]
user32.SetActiveWindow.restype = wintypes.HWND
user32.SetFocus.argtypes = [wintypes.HWND]
user32.SetFocus.restype = wintypes.HWND
user32.GetFocus.restype = wintypes.HWND
user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
user32.AttachThreadInput.restype = wintypes.BOOL
GWL_STYLE = -16
WS_CAPTION, WS_THICKFRAME, WS_SYSMENU, WS_MINIMIZEBOX, WS_MAXIMIZEBOX = 0x00C00000, 0x00040000, 0x00080000, 0x00020000, 0x00010000
WS_VISIBLE, WS_POPUP = 0x10000000, 0x80000000
WS_CHILD = 0x40000000
SWP_NOZORDER, SWP_FRAMECHANGED, SWP_SHOWWINDOW = 0x4, 0x20, 0x40

GAME_ID = "1a96545b"
PLAYER_NAME = "CarPackStudio"   # gamertag shown in game; Azimuth still stores the profile under saves\player-1


def find_windows(pid):
    out = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, _):
        p = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(p))
        if p.value == pid and user32.IsWindowVisible(hwnd):
            buf = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(hwnd, buf, 256)
            r = wintypes.RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(r))
            out.append((hwnd, buf.value, r.right - r.left, r.bottom - r.top))
        return True

    user32.EnumWindows(cb, 0)
    return out


def send_key(hwnd, vk):
    user32.SetForegroundWindow(hwnd)
    scan = user32.MapVirtualKeyW(vk, 0)
    user32.keybd_event(vk, scan, 0, 0)
    time.sleep(0.05)
    user32.keybd_event(vk, scan, 2, 0)


def focus_game_window(hwnd, host) -> bool:
    """Transfer real keyboard focus across input threads without stealing another app's focus."""
    foreground = user32.GetForegroundWindow()
    if not hwnd or not user32.IsWindow(hwnd) or user32.GetAncestor(foreground, 2) not in (host, hwnd):
        return False
    thread = ctypes.windll.kernel32.GetCurrentThreadId()
    game_thread = user32.GetWindowThreadProcessId(hwnd, None)
    if not game_thread:
        return False
    attached = game_thread != thread
    if attached and not user32.AttachThreadInput(thread, game_thread, True):
        return False
    try:
        user32.SetForegroundWindow(host)
        user32.SetActiveWindow(host)
        user32.SetFocus(hwnd)
        return user32.GetFocus() == hwnd
    finally:
        if attached:
            user32.AttachThreadInput(thread, game_thread, False)


class Runtime:
    def __init__(self, settings):
        self.s = settings
        self.proc = None
        self.hwnd = None
        self.saved_style = None
        self.extra_cars = []   # [(pack car id, donor car id)] to place into the generated save
        self.last_car = None   # car id the save should start in (the car chosen for testing)
        self._command_lock = threading.Lock()

    @property
    def dir(self):
        if self.s["runtime_mode"] == "existing" and self.s["azimuth_dir"]:
            return self.s["azimuth_dir"]
        return self.s["sandbox_dir"]

    @property
    def sandbox(self):
        return not (self.s["runtime_mode"] == "existing" and self.s["azimuth_dir"])

    @property
    def mods_dir(self):
        return os.path.join(self.dir, "fh2-mods")

    @property
    def exe(self):
        return os.path.join(self.dir, "azimuth.exe")

    @property
    def mount(self):
        return self.s["mount"]

    @property
    def game_folder(self):
        return os.path.dirname(self.mount)

    def prepare(self, log=print):
        src = config.runtime_src()
        dst = self.dir
        if self.running():
            raise ValueError("Stop the wrapper before preparing its runtime.")
        if self.sandbox and self.s.get("dependency_source") and missing_dependencies(dst):
            import_dependencies(self.s["dependency_source"], dst, log)
        require_dependencies(dst)
        os.makedirs(dst, exist_ok=True)
        for relative in BUNDLED_RUNTIME:
            s, t = os.path.join(src, relative), os.path.join(dst, relative)
            name = os.path.basename(relative).lower()
            if not self.sandbox and name.startswith("carpackstudio"):
                continue
            if name.endswith(".ini") and os.path.exists(t):
                continue
            if os.path.normcase(os.path.abspath(s)) == os.path.normcase(os.path.abspath(t)):
                continue
            if not os.path.isfile(s):
                raise FileNotFoundError("Missing bundled Studio asset: " + s)
            if not os.path.exists(t) or os.path.getsize(t) != os.path.getsize(s) or os.path.getmtime(t) < os.path.getmtime(s):
                os.makedirs(os.path.dirname(t), exist_ok=True)
                shutil.copy2(s, t)
        if self.sandbox:
            self._write_launcher_ini()
            self._write_extra_options()
            self.install_save(log)
        self._write_loader_ini()
        self._write_showfps_ini()
        self._write_dashboard_ini()

    def install_save(self, log=print):
        """Always regenerate the sandbox test save from the shipped template."""
        from . import save
        saves = os.path.join(self.dir, save.TITLE_DIR)
        gdb = os.path.join(self.mount, "media", "stripped", "gamedbRC.slt")
        extra = sorted(self.extra_cars)
        key = json.dumps([os.path.getmtime(gdb), extra, int(self.s.get("save_credits", 999999999)), self.last_car, PLAYER_NAME, 3])
        stamp = os.path.join(saves, "cps_save.json")
        user = os.path.join(saves, "player-1", save.USER_DIR)
        # always rebuild: the game replaces an oversized garage with a single fallback car
        _ = (user, stamp)
        os.makedirs(saves, exist_ok=True)
        save.build_ultimate_save(self.mount, saves, "player-1", int(self.s.get("save_credits", 999999999)),
                                 extra, PLAYER_NAME, self.last_car, log)
        with open(stamp, "w") as f:
            f.write(key)

    def _write_extra_options(self):
        """Sandbox only: start in free roam, preserving unrelated Extra Options settings."""
        ini = os.path.join(self.dir, "FH2_ExtraOptions.ini")
        if not os.path.exists(ini):
            return
        want = {"SkipCarMeetOnStartup": 1, "SpawnAtFestival": 1 if self.s.get("spawn_festival", True) else 0}
        lines = open(ini, encoding="utf-8", errors="replace").read().splitlines()
        sec, seen, out = None, set(), []
        for l in lines:
            m = re.match(r"\s*\[(.+?)\]", l)
            if m:
                if sec == "Frontend":
                    out += ["%s=%d" % (k, v) for k, v in want.items() if k not in seen]
                    seen |= set(want)
                sec = m.group(1)
            elif sec == "Frontend":
                k = l.split("=", 1)[0].strip()
                if k in want and "=" in l and not l.lstrip().startswith(";"):
                    l = "%s=%d" % (k, want[k])
                    seen.add(k)
            out.append(l)
        if sec == "Frontend":
            out += ["%s=%d" % (k, v) for k, v in want.items() if k not in seen]
        elif "Frontend" not in "".join(lines):
            out += ["[Frontend]"] + ["%s=%d" % kv for kv in want.items()]
        with open(ini, "w", encoding="utf-8", newline="") as f:
            f.write("\r\n".join(out) + "\r\n")
        path = os.path.join(self.dir, "FH2_ExtraOptions.dat")
        if os.path.exists(path):
            with open(path, "rb") as f:
                data = bytearray(f.read())
            if len(data) != 164 or struct.unpack_from("<II", data) != (0x32444846, 19):
                raise ValueError("Unsupported Extra Options settings layout: %s" % path)
            # Version 19's SpawnLocation (index 19) overrides the legacy INI keys.
            struct.pack_into("<i", data, 8 + 19 * 4, 2 if self.s.get("spawn_festival", True) else 1)
            with open(path, "wb") as f:
                f.write(data)

    def _write_launcher_ini(self):
        data = os.path.join(self.dir, "data")
        os.makedirs(data, exist_ok=True)
        ini = os.path.join(data, "azimuth.ini")
        settings = configparser.ConfigParser(interpolation=None)
        settings.optionxform = str
        if os.path.exists(ini):
            with open(ini, "rb") as f:
                content = f.read()
            encoding = "utf-16" if content.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig"
            settings.read_string(content.decode(encoding))
        title = "title-" + GAME_ID
        for section in ("launcher", title):
            if not settings.has_section(section):
                settings.add_section(section)
        search_roots = [path for path in settings["launcher"].get("look_in", "").split("|") if path]
        base_root = os.path.dirname(self.game_folder)
        if os.path.normcase(os.path.normpath(base_root)) not in {
                os.path.normcase(os.path.normpath(path)) for path in search_roots}:
            search_roots.append(base_root)
        settings["launcher"].update({
            "game": GAME_ID, "display": "windowed", "player_name": PLAYER_NAME,
            "look_in": "|".join(search_roots),
        })
        settings["launcher"].setdefault("settings_version", "5")
        defaults = {"name": "Forza Horizon 2", "frame_rate": "60", "vsync": "off",
                    "graphics": "custom", "language": "en-GB", "speech": "en-US", "volume": "80"}
        for key, value in defaults.items():
            settings[title].setdefault(key, value)
        settings[title]["folder"] = self.game_folder
        with open(ini, "w", encoding="utf-16", newline="\r\n") as f:
            settings.write(f, space_around_delimiters=False)

    def _write_loader_ini(self):
        os.makedirs(self.mods_dir, exist_ok=True)
        with open(os.path.join(self.mods_dir, "CarPackStudio.ini"), "w", encoding="utf-8", newline="") as f:
            f.write("[CrashGuard]\r\nEnabled=1\r\nRecover=%d\r\nMaxRecoveries=25\r\n"
                    "[Boot]\r\nAutoStart=%d\r\nDelayMs=2000\r\n[Car]\r\nForceId=%d\r\nExperimentalReload=%d\r\nTravelTestBinds=%d\r\n"
                    % (1 if self.s.get("crash_recover", True) else 0,
                                           1 if self.s.get("boot_assist", True) else 0,
                                           self.last_car if self.sandbox and self.last_car else -1,
                                           int(self.sandbox),
                                           int(self.s.get("travel_test_binds", True))))

    def _write_showfps_ini(self):
        p = os.path.join(self.mods_dir, "ShowFPS.ini")
        if self.sandbox and os.path.exists(p):
            txt = open(p, encoding="utf-8", errors="replace").read()
            txt = re.sub(r"(?mi)^Enabled=.*$", "Enabled=%d" % (1 if self.s.get("show_fps", True) else 0), txt, count=1)
            with open(p, "w", encoding="utf-8") as f:
                f.write(txt)

    def _write_dashboard_ini(self):
        p = os.path.join(self.mods_dir, "VirtualDashboard.ini")
        if os.path.exists(p):
            txt = open(p, encoding="utf-8", errors="replace").read()
            if re.search(r"(?mi)^Port=", txt):
                with open(p, "w", encoding="utf-8") as f:
                    f.write(re.sub(r"(?mi)^Port=.*$", "Port=%d" % self.s["telemetry_port"], txt))

    def running(self):
        return self.proc is not None and self.proc.poll() is None

    def _pause_xml(self):
        return os.path.join(self.mount, "mediaoverride", "stateflow", "pause.xml")

    def _backup_swap_flow(self):
        """Protect the user's override before the native experimental flow modifies it."""
        p = self._pause_xml()
        orig, absent = p + ".cps_orig", p + ".cps_absent"
        self._restore_swap_flow()
        os.makedirs(os.path.dirname(p), exist_ok=True)
        if os.path.exists(p):
            shutil.copy2(p, orig)
        else:
            with open(absent, "x"):
                pass

    def _restore_swap_flow(self):
        p = self._pause_xml()
        orig, absent = p + ".cps_orig", p + ".cps_absent"
        if os.path.exists(orig):
            os.replace(orig, p)
        elif os.path.exists(absent):
            if os.path.exists(p):
                os.remove(p)
            os.remove(absent)

    def launch(self):
        if self.running():
            return
        arguments = self.s.get("launch_arguments", config.DEFAULTS["launch_arguments"])
        if not isinstance(arguments, str) or "\0" in arguments:
            raise ValueError("Azimuth launch arguments must be text without null characters.")
        require_dependencies(self.dir)
        # Preserve Windows quoting and backslashes exactly; no shell interprets the arguments.
        command = subprocess.list2cmdline([self.exe])
        if arguments.strip():
            command += " " + arguments.strip()
        if self.sandbox:
            self._restore_swap_flow()
            self._backup_swap_flow()
        try:
            self.proc = subprocess.Popen(command, cwd=self.dir, shell=False)
        except (OSError, ValueError):
            if self.sandbox:
                self._restore_swap_flow()
            raise
        self.hwnd = None

    def stop(self):
        if self.running():
            self.proc.terminate()
            try:
                self.proc.wait(5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()
        self.hwnd = None
        if self.sandbox:
            self._restore_swap_flow()

    def game_window(self):
        if not self.running():
            return None
        if self.hwnd and user32.IsWindow(self.hwnd):
            return self.hwnd
        wins = [w for w in find_windows(self.proc.pid) if w[2] > 200 and w[3] > 150]
        if wins:
            self.hwnd = max(wins, key=lambda w: w[2] * w[3])[0]
        return self.hwnd

    def strip_frame(self, hwnd):
        self.saved_style = user32.GetWindowLongPtrW(hwnd, GWL_STYLE)
        st = self.saved_style & ~(WS_CAPTION | WS_THICKFRAME | WS_SYSMENU | WS_MINIMIZEBOX | WS_MAXIMIZEBOX | WS_POPUP)
        user32.SetWindowLongPtrW(hwnd, GWL_STYLE, st | WS_VISIBLE)

    def restore_frame(self, hwnd):
        if self.saved_style is not None:
            user32.SetWindowLongPtrW(hwnd, GWL_STYLE, self.saved_style)
        user32.SetWindowPos(hwnd, 0, 100, 100, 1280, 720, SWP_NOZORDER | SWP_FRAMECHANGED | SWP_SHOWWINDOW)

    def telemetry(self):
        try:
            with urllib.request.urlopen("http://127.0.0.1:%d/api" % self.s["telemetry_port"], timeout=0.4) as r:
                return json.loads(r.read().decode("utf-8", "replace"))
        except Exception:
            return None

    def car_id(self, media):
        """Resolve the current car from the active database, including installed pack cars."""
        for layer in ("mediaoverride", "mediapc", "media"):
            path = os.path.join(self.mount, layer, "stripped", "gamedbRC.slt")
            if os.path.isfile(path):
                with closing(sqlite3.connect("file:%s?mode=ro" % path.replace("\\", "/"), uri=True)) as db:
                    row = db.execute("SELECT Id FROM Data_Car WHERE MediaName=? COLLATE NOCASE", (media,)).fetchone()
                return row[0] if row else None
        raise FileNotFoundError("Cannot find the active game database")

    def session(self):
        try:
            with open(os.path.join(self.mods_dir, "platform-session.json"), encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return None

    def command(self, line):
        """Send one console line to the mod platform's command pipe."""
        if not self._command_lock.acquire(timeout=2):
            return "error: mod platform connection busy; no command was sent"
        try:
            proc = self.proc
            sess = self.session()
            if not sess or proc is None or proc.poll() is not None:
                return "error: game is not running"
            if not isinstance(sess, dict) or sess.get("pid") != proc.pid:
                return "error: mod platform session is not ready for this game process"
            try:
                return pipe_request(sess["pipe"], line)
            except (OSError, ValueError, KeyError, TypeError) as ex:
                return "error: mod platform connection unavailable: %s" % ex
        finally:
            self._command_lock.release()

    def log_files(self):
        return {
            "platform": os.path.join(self.mods_dir, "platform.log"),
            "critical": os.path.join(self.mods_dir, "CarPackStudio-critical.log"),
            "extraoptions": os.path.join(self.dir, "FH2_ExtraOptions.log"),
            "azimuth": os.path.join(self.dir, "data", "logs", "azimuth.log"),
        }
