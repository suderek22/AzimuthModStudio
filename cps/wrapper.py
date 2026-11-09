"""Wrapper tab: embeds the running game, telemetry overlay and console."""
import os
import re
import shlex
import shutil
import sqlite3
import time
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QThread, Signal, QRectF, QSize
from PySide6.QtGui import QColor, QPainter, QPen, QFont, QIcon
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
                               QSplitter, QCheckBox, QStackedLayout, QApplication, QFileDialog)

from . import theme, config
from .runtime_state import parse_track_state
from .volume import validate_volume, process_volumes
from .console import Console, ConsoleWindow
from .overlay import TelemetryOverlay
from .runtime import Runtime, user32, WS_CHILD, WS_VISIBLE, GWL_STYLE, send_key, focus_game_window
from .travel import (TRACKS, TeleportPlace, resolve_destination, resolve_track,
                     resolve_place_track, load_places, place_key, validate_place_name,
                     parse_player_position, teleport_entries, track_label, add_place, remove_place)
from .command_scripts import CommandScriptRecorder, CommandScriptStore

VK_RETURN = 0x0D
STUDIO_COMMANDS = {"time", "freeze", "weather", "status", "recover", "echo", "car", "animation", "animations",
                   "input", "trace", "pipelines", "spawn", "drivatar"}
SWP_NOZORDER = 0x4


class GameContainer(QWidget):
    resized = Signal()

    def __init__(self):
        super().__init__()
        self.setAttribute(Qt.WA_NativeWindow, True)
        self.setAttribute(Qt.WA_OpaquePaintEvent, True)
        self.setStyleSheet("background:#000;")
        self.setMinimumSize(320, 180)
        self.setFocusPolicy(Qt.StrongFocus)
        self.child = None

    def embed(self, rt, hwnd):
        rt.strip_frame(hwnd)
        st = user32.GetWindowLongPtrW(hwnd, GWL_STYLE)
        user32.SetParent(hwnd, int(self.winId()))
        user32.SetWindowLongPtrW(hwnd, GWL_STYLE, (st | WS_CHILD | WS_VISIBLE) & ~0x80000000)
        self.child = hwnd
        self.fit()
        user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, 0x1 | 0x2 | SWP_NOZORDER | 0x20 | 0x40)
        self.setFocus(Qt.OtherFocusReason)
        QTimer.singleShot(0, self.focus_game)

    def focus_game(self):
        if self.child:
            return focus_game_window(self.child, int(self.window().winId()))
        return False

    def focusInEvent(self, event):
        super().focusInEvent(event)
        self.focus_game()

    def mousePressEvent(self, event):
        super().mousePressEvent(event)
        self.focus_game()

    def release(self, rt):
        if self.child and user32.IsWindow(self.child):
            user32.SetParent(self.child, 0)
            rt.restore_frame(self.child)
        self.child = None

    def fit(self):
        if self.child and user32.IsWindow(self.child):
            dpr = self.devicePixelRatioF()
            user32.SetWindowPos(self.child, 0, 0, 0, int(self.width() * dpr), int(self.height() * dpr),
                                SWP_NOZORDER | 0x40)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.fit()


class Poller(QThread):
    data = Signal(object)
    swap_data = Signal(object)
    native_data = Signal(object)

    def __init__(self, rt):
        super().__init__()
        self.rt = rt
        self.alive = True
        self.swap_target = None
        self.refresh_native = False

    def run(self):
        next_swap_poll = 0
        next_native_poll = 0
        while self.alive:
            self.data.emit(self.rt.telemetry() if self.rt.running() else None)
            target = self.swap_target
            if target is not None and self.rt.running() and time.monotonic() >= next_swap_poll:
                reply = self.rt.command("CarPackStudio car status")
                self.swap_data.emit((target, reply))
                next_swap_poll = time.monotonic() + 1
            if self.rt.running() and (self.refresh_native or time.monotonic() >= next_native_poll):
                self.refresh_native = False
                proc = self.rt.proc
                if proc is not None:
                    self.native_data.emit({"pid": proc.pid, "track": self.rt.command("CarPackStudio track status"),
                                           "spawn": self.rt.command("CarPackStudio spawn list")})
                next_native_poll = time.monotonic() + 1
            time.sleep(0.1)


class WrapperTab(QWidget):
    status = Signal(str)
    running_changed = Signal(bool)
    native_snapshot = Signal(object)
    places_changed = Signal()

    def __init__(self, settings, prepare_cb):
        super().__init__()
        self.s = settings
        self.rt = Runtime(settings)
        self.prepare_cb = prepare_cb     # builds/installs the project's pack before launch
        self.after_cb = None             # called with a write function after the game is stopped
        self.car_cb = None               # returns the car id selected in the tool (used by ▶ Run)
        self.forced_car = None           # car id requested by "Test in game"
        self.car = None
        self.active_car = None
        self.car_title_cb = None
        self.swap_target = None
        self.swap_previous_id = None
        self.swap_started = 0
        self.swap_timed_out = False
        self.embedded = False
        self.detached = False
        self.launch_t = 0
        self.last_enter = 0
        self.log_pos = {}
        self.driving = False
        self.debug_targets = set()
        self.log_pending = {}
        self.console_window = None
        self.console_sizes = [620, 160]
        self.scripts_window = None
        self.command_scripts = CommandScriptStore(os.path.join(config.APPDATA, "scripts", "commands"))
        self.command_recorder = CommandScriptRecorder(self.command_scripts)
        self.command_script_timer = QTimer(self)
        self.command_script_timer.setInterval(20)
        self.command_script_timer.timeout.connect(self._advance_command_script)
        self.command_playback = None
        self.command_playback_index = 0
        self.command_playback_started = 0
        self.places_window = None
        self.pending_navigation = None
        self._reported_running = False
        self._volume_pending = None
        self._volume_next_try = 0
        self._volume_requested_at = 0
        self._input_expected = set()
        self._input_ready = set()
        self._input_wait_started = None

        self.run_btn = QPushButton("▶ Run")
        self.run_btn.setObjectName("primary")
        self.run_btn.clicked.connect(self.start)
        self.stop_btn = QPushButton("■ Stop")
        self.stop_btn.clicked.connect(self.stop)
        self.recover_btn = QPushButton("Recover previous car")
        self.recover_btn.setToolTip("After a stalled swap, restart in the previous car at the normal spawn point.")
        self.recover_btn.clicked.connect(self.recover_swap)
        self.recover_btn.hide()
        self.detach_btn = QPushButton()
        self.detach_btn.setFixedSize(28, 28)
        self.detach_btn.setIconSize(QSize(18, 18))
        self._set_game_detached(False)
        self.detach_btn.clicked.connect(self.toggle_detach)
        self.boot = QCheckBox("Skip title screen")
        self.boot.setChecked(settings.get("boot_assist", True))
        self.show_overlay = QCheckBox("Telemetry overlay")
        self.show_overlay.setChecked(False)
        self.show_fps = QCheckBox("ShowFPS")
        self.show_fps.setToolTip("ShowFPS mod performance overlay (applies on the next run; F11 toggles it in game)")
        self.show_fps.setChecked(settings.get("show_fps", True))
        self.car_lbl = QLabel("Car: (none selected)")
        self.state = QLabel("Stopped")
        self.state.setStyleSheet("color:%s;" % theme.FG_DIM)
        bar = QHBoxLayout()
        bar.setContentsMargins(6, 4, 6, 4)
        bar.setSpacing(10)
        for checkbox in (self.boot, self.show_overlay, self.show_fps):
            checkbox.setStyleSheet("QCheckBox { spacing:6px; }")
        for w in (self.run_btn, self.stop_btn, self.recover_btn, self.detach_btn, self.boot, self.show_overlay,
                  self.show_fps):
            bar.addWidget(w)
        bar.addStretch()
        self.screenshot_btn = QPushButton()
        self.screenshot_btn.setIcon(QIcon(os.path.join(config.ROOT, "resources", "screenshot.svg")))
        self.screenshot_btn.setIconSize(QSize(20, 20))
        self.screenshot_btn.setFixedSize(32, 28)
        self.screenshot_btn.setToolTip("Save the game client at its native pixel resolution to %APPDATA%\\CarPackStudio\\screenshots")
        self.screenshot_btn.setAccessibleName("Screenshot")
        self.screenshot_btn.setEnabled(False)
        self.screenshot_btn.clicked.connect(self.take_screenshot)
        bar.addWidget(self.screenshot_btn)
        details = QHBoxLayout()
        details.setContentsMargins(6, 2, 6, 6)
        details.setSpacing(16)
        self.car_lbl.setWordWrap(True)
        details.addWidget(self.car_lbl, 1)
        details.addWidget(self.state)

        self.container = GameContainer()
        self.placeholder = QLabel("Press ▶ Run to start the game inside the tool.\n"
                                  "Azimuth 1.0.2 · FH2 Mod Platform 2.8.0 · Extra Options 0.3.2")
        self.placeholder.setAlignment(Qt.AlignCenter)
        self.placeholder.setStyleSheet("color:%s; background:#000;" % theme.FG_DIM)
        self.stack = QWidget()
        sl = QStackedLayout(self.stack)
        sl.setStackingMode(QStackedLayout.StackAll)
        sl.addWidget(self.container)
        sl.addWidget(self.placeholder)
        self.sl = sl
        self.overlay = TelemetryOverlay(self.container)
        self.console = Console()
        self.console.submit.connect(self.on_command)
        self.console.detach_requested.connect(lambda: self.send("console 1" if self.console_window is None else "console 0"))
        split = QSplitter(Qt.Vertical)
        split.addWidget(self.stack)
        split.addWidget(self.console)
        split.setSizes([620, 160])
        split.setStretchFactor(0, 1)
        self.console_split = split
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addLayout(bar)
        lay.addLayout(details)
        lay.addWidget(split)

        self.poller = Poller(self.rt)
        self.poller.data.connect(self.on_telemetry)
        self.poller.swap_data.connect(self.on_swap_status)
        self.poller.native_data.connect(self.on_native_data)
        self.poller.start()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(400)
        self._show_placeholder(True)
        self.refresh_optional_mods()

    # ------------------------------------------------------------- state
    def _show_placeholder(self, on):
        self.placeholder.setVisible(on)
        if on:
            self.placeholder.raise_()

    def set_car(self, media, title):
        self.car = media
        if not (self.rt.running() and self.active_car):
            self.car_lbl.setText("Car: %s (%s)" % (title, media))

    def start(self):
        if self.rt.running():
            return
        if not self.s.get("mount"):
            self.console.write("Set the game Mount path in Settings first.", theme.RED)
            return
        self.rt.s = self.s
        cid = self.forced_car or (self.car_cb() if self.car_cb else None)
        self.forced_car = None
        if cid:
            self.rt.last_car = cid
            self.console.write("Starting in car id %s (%s)" % (cid, self.car or "?"), theme.FG_DIM)
        self.s["boot_assist"] = self.boot.isChecked()
        self.s["show_fps"] = self.show_fps.isChecked()
        try:
            self.console.write("Preparing runtime in %s …" % self.rt.dir, theme.FG_DIM)
            if self.prepare_cb:
                self.prepare_cb(self.console.write)
            self.rt.prepare(lambda m: self.console.write(m, theme.ORANGE))
            self.log_pos = {k: (os.path.getsize(v) if os.path.exists(v) else 0) for k, v in self.rt.log_files().items()}
            self.log_pending.clear()
            self.rt.launch()
        except Exception as ex:
            self.console.write("Cannot start: %s" % ex, theme.RED)
            return
        self.launch_t = time.time()
        self.embedded = False
        self.detached = False
        self.driving = False
        self._set_game_detached(False)
        self.active_car = None
        self.state.setText("Starting…")
        self.console.write("azimuth.exe started (pid %d)" % self.rt.proc.pid, theme.GREEN)
        self._report_running()

    def stop(self):
        if self.scripts_window and (self.scripts_window.capturing or self.scripts_window.playing) and self.rt.running():
            self.scripts_window.stop()
        if self.embedded:
            self.container.child = None
        self.rt.stop()
        self._clear_swap()
        self.embedded = False
        self._show_placeholder(True)
        self.state.setText("Stopped")
        self._report_running()
        if self.after_cb:
            self.after_cb(self.console.write)

    def toggle_detach(self):
        hwnd = self.rt.game_window()
        if not hwnd:
            return
        if self.detached:
            self.container.embed(self.rt, hwnd)
            self.detached = False
            self.embedded = True
            self._show_placeholder(False)
            self._set_game_detached(False)
        else:
            self.container.release(self.rt)
            self.detached = True
            self.embedded = False
            self._show_placeholder(True)
            self.placeholder.setText("Game is running in a detached window.\nPress the Attach icon to bring it back.")
            self._set_game_detached(True)

    def _set_game_detached(self, detached):
        label = "Attach" if detached else "Detach"
        self.detach_btn.setIcon(QIcon(os.path.join(config.ROOT, "resources",
                                                 "attach.svg" if detached else "detach.svg")))
        self.detach_btn.setAccessibleName(label + " game window")
        self.detach_btn.setToolTip(label + " game window")

    # ------------------------------------------------------------- periodic
    def tick(self):
        running = self.rt.running()
        self._report_running()
        self.refresh_optional_mods()
        if running:
            self._pump_logs()
        if running and not self.embedded and not self.detached:
            hwnd = self.rt.game_window()
            if hwnd and self._input_window_ready():
                self.container.embed(self.rt, hwnd)
                self.embedded = True
                self._show_placeholder(False)
                self.state.setText("Running")
                self.console.write("game window embedded", theme.FG_DIM)
        if not running and (self.embedded or self.detached or self.rt.proc):
            code = self.rt.proc.returncode if self.rt.proc else None
            if self.embedded or self.detached or code is not None:
                self.embedded = self.detached = False
                self.container.child = None
                self._show_placeholder(True)
                self.placeholder.setText("Game exited (code %s). Press ▶ Run to start again." % code)
                self.state.setText("Stopped")
                if code not in (0, None):
                    self._critical_exit(code)
                self.rt.proc = None
                self._clear_swap()
        if running:
            self._check_swap_timeout()
            self._apply_volume()
        self.overlay.follow(self.embedded and self.show_overlay.isChecked())
        if self.pending_navigation and time.monotonic() - self.pending_navigation[2] > 95:
            self._cancel_navigation("Map travel timed out; the follow-up action was not sent. Use track status to inspect the game.")

    def _report_running(self):
        running = self.rt.running()
        self.screenshot_btn.setEnabled(running)
        if self._reported_running != running:
            self._reported_running = running
            if not running and self.pending_navigation:
                self._cancel_navigation("Wrapper stopped; pending map action cancelled.")
            self.running_changed.emit(running)
            self._volume_pending = self.rt.proc.pid if running else None
            self._volume_requested_at = time.monotonic()
            self._volume_next_try = 0
            self._input_expected = {name for dll, name in (("FH2Mouse.dll", "FH2 Mouse & Keyboard"),
                                                          ("FH2NoPause.dll", "NoPause"))
                                    if running and (Path(self.rt.mods_dir) / dll).is_file()}
            self._input_ready.clear()
            self._input_wait_started = None

    def refresh_optional_mods(self):
        installed = (Path(self.rt.mods_dir) / "ShowFPS.dll").is_file()
        self.show_fps.setEnabled(installed)
        self.show_fps.setToolTip("ShowFPS setting applies on the next run; F11 toggles it in game."
                                if installed else "Optional ShowFPS mod is not installed. Use Mods > Import dependencies.")

    def _input_window_ready(self):
        missing = self._input_expected - self._input_ready
        if not missing:
            return True
        if self._input_wait_started is None:
            self._input_wait_started = time.monotonic()
        if time.monotonic() - self._input_wait_started >= 20:
            self.console.notice("Input initialization was not confirmed for %s; embedding anyway. "
                                "If controls do not respond, detach/attach and inspect platform.log." %
                                ", ".join(sorted(missing)), theme.ORANGE)
            self._input_expected.clear()
            return True
        self.state.setText("Preparing input...")
        self.placeholder.setText("Waiting for %s to discover the game window before embedding..." %
                                 ", ".join(sorted(missing)))
        return False

    def set_volume(self, value):
        value = validate_volume(value)
        self.s["wrapper_volume"] = value
        if not self.rt.running():
            self.console.notice("Wrapper volume saved: %.3g (applies on the next run)." % value)
            return
        self._volume_pending = self.rt.proc.pid
        self._volume_requested_at = time.monotonic()
        self._volume_next_try = 0
        self._apply_volume()

    def _apply_volume(self):
        if self._volume_pending is None or time.monotonic() < self._volume_next_try:
            return
        if not self.rt.running() or self.rt.proc.pid != self._volume_pending:
            self._volume_pending = None
            return
        try:
            value = validate_volume(self.s.get("wrapper_volume", 1.0))
            if process_volumes(self._volume_pending, value):
                self._volume_pending = None
                self.console.notice("Wrapper volume: %.3g" % value)
            elif time.monotonic() - self._volume_requested_at > 90:
                raise OSError("No audio session appeared for this wrapper process within 90 seconds.")
            else:
                if self._volume_next_try == 0:
                    self.console.notice("Wrapper volume %.3g pending; waiting for the game's audio session." % value)
                self._volume_next_try = time.monotonic() + 1
        except (OSError, ValueError) as ex:
            self._volume_pending = None
            self.console.notice("error: cannot set wrapper volume: %s" % ex, theme.RED)

    def _cancel_navigation(self, message):
        self.pending_navigation = None
        self.console.notice(message, theme.RED)
        self.status.emit(message)

    def navigate(self, track_id, followup=None):
        """Queue a GUI action only after the native map flow confirms settled arrival."""
        try:
            if not self.rt.running():
                raise ValueError("Start the wrapper and enter free roam first.")
            if self.pending_navigation:
                raise ValueError("Wait for the pending map action to finish.")
            if track_id not in TRACKS.values():
                raise ValueError("Native map travel currently supports Anthem (542) and TheIsland (549).")
            state = parse_track_state(self.rt.command("CarPackStudio track status"))
            if not state.idle:
                raise ValueError("Wait for settled free roam before changing maps or teleporting.")
            if state.current == track_id:
                if followup:
                    self.send(followup)
                else:
                    self.console.notice("Map already loaded: %s (%d)" % (track_label(track_id), track_id))
                return
            reply = self.rt.command("CarPackStudio track %d" % track_id)
            self.console.notice(reply, theme.RED if reply.startswith("error:") else None)
            if not reply.startswith("track queued:"):
                return
            self.pending_navigation = (track_id, followup, time.monotonic())
            self.state.setText("Travelling to %s..." % track_label(track_id))
            self.poller.refresh_native = True
            self.console.notice("Waiting for %s before %s." %
                                (track_label(track_id), followup or "finishing map travel"))
        except ValueError as ex:
            self.console.notice("error: %s" % ex, theme.RED)

    def on_native_data(self, snapshot):
        if not self.rt.running() or snapshot["pid"] != self.rt.proc.pid:
            return
        self.native_snapshot.emit(snapshot)
        if not self.pending_navigation:
            return
        track_id, followup, _ = self.pending_navigation
        try:
            if snapshot["track"].startswith("error:"):
                return  # A transient pipe outage must not execute or discard queued travel.
            state = parse_track_state(snapshot["track"])
            if state.timed_out or any(word in state.stage.casefold() for word in ("failed", "error", "expired")):
                raise ValueError("Map travel failed: " + snapshot["track"])
            if state.queued not in (-1, track_id) or state.target not in (-1, track_id):
                raise ValueError("Another map travel replaced the pending GUI action.")
            if state.current == track_id and state.idle:
                self.pending_navigation = None
                self.console.notice("Arrived at %s." % track_label(track_id))
                if followup:
                    self.send(followup)
        except ValueError as ex:
            self._cancel_navigation(str(ex) + " Follow-up action cancelled.")

    def take_screenshot(self):
        from .screenshots import capture_game
        try:
            path, width, height = capture_game(self.rt)
            message = "Screenshot saved (%d x %d): %s" % (width, height, path)
            self.console.notice(message, theme.GREEN)
            self.status.emit(message)
        except (OSError, ValueError) as ex:
            message = "Cannot capture screenshot: %s" % ex
            self.console.notice(message, theme.RED)
            self.status.emit(message)

    def on_telemetry(self, t):
        self.overlay.update_data(t)
        self.overlay.follow(self.embedded and self.show_overlay.isChecked())
        if t and t.get("valid") and t.get("car"):
            media = t["car"]
            if media != self.active_car:
                title = self.car_title_cb(media) if self.car_title_cb else media
                self.car_lbl.setText("Car: %s (%s)" % (title, media))
                if self.driving:
                    self.console.write("car changed: %s" % media, theme.GREEN)
                self.active_car = media
            if self.swap_target is None and self.pending_navigation is None:
                self.state.setText("Driving: %s" % media)
        if t and t.get("valid") and not self.driving:
            self.driving = True
            if self.swap_target is None and self.pending_navigation is None:
                self.state.setText("Driving: %s" % t.get("car", ""))
            self.console.write("in game – telemetry live", theme.GREEN)
            if self.embedded and self.container.hasFocus():
                self.container.focus_game()

    def _pump_logs(self):
        for key, path in self.rt.log_files().items():
            if not os.path.exists(path):
                continue
            size = os.path.getsize(path)
            pos = self.log_pos.get(key, 0)
            if size < pos:
                pos = 0
                self.log_pending.pop(key, None)
            if size > pos:
                with open(path, "rb") as f:
                    f.seek(pos)
                    chunk = f.read(min(size - pos, 65536)).decode("utf-8", "replace")
                self.log_pos[key] = pos + min(size - pos, 65536)
                chunk = self.log_pending.pop(key, "") + chunk
                lines = chunk.split("\n")
                self.log_pending[key] = lines.pop()
                for line in lines:
                    self._log_line(key, line.rstrip("\r"))

    def _log_line(self, key, line):
        if key == "platform":
            if "[FH2 Mouse & Keyboard]" in line and "watching the game window" in line:
                self._input_ready.add("FH2 Mouse & Keyboard")
            elif "[NoPause]" in line and "watching game window" in line:
                self._input_ready.add("NoPause")
        if re.search(r"\[CarPackStudio\]\s+command:\s+(?:(?:car|input|track) status|spawn list)\s*$", line, re.I):
            return
        match = re.search(r"\[([^\]]+)\]", line)
        resource = match.group(1) if key == "platform" and match else {
            "azimuth": "Azimuth", "extraoptions": "ExtraOptions", "critical": "CarPackStudio"
        }.get(key, "Platform")
        if "[AzimuthPipeline]" in line:
            resource = "Azimuth"
        bad = any(word in line.lower() for word in ("error", "fail", "crash", "cannot", "invalid", "refused"))
        if self.debug_targets and resource.casefold() not in self.debug_targets and not bad and key != "critical":
            return
        if key == "azimuth" and "azimuth" not in self.debug_targets and not bad:
            return
        text = line if key == "platform" else "[%s] %s" % (resource, line)
        self.console.write(text, theme.RED if key == "critical" else theme.ORANGE if bad else theme.FG_DIM,
                           resource=resource)

    def set_console_detached(self, detached):
        if detached:
            if self.console_window is None:
                self.console_sizes = self.console_split.sizes()
                self.console_window = ConsoleWindow(self.window())
                self.console_window.setWindowTitle("Car Pack Studio - Console")
                self.console_window.setMinimumSize(640, 400)
                self.console_window.setSizeGripEnabled(True)
                self.console_window.attach_requested.connect(lambda: self.set_console_detached(False))
                layout = QVBoxLayout(self.console_window)
                layout.setContentsMargins(0, 0, 0, 0)
                layout.addWidget(self.console)
                self.console_window.resize(1000, 700)
                self.console.set_detached(True)
            self.console_window.show()
            self.console_window.raise_()
        elif self.console_window is not None:
            window, self.console_window = self.console_window, None
            self.console_split.addWidget(self.console)
            self.console.show()
            self.console_split.setSizes(self.console_sizes)
            window.hide()
            window.deleteLater()
            self.console.set_detached(False)

    def _console_command(self, line):
        first = line.split(None, 1)[0].lower() if line else ""
        if first not in ("filter", "debug", "console", "tp", "track", "event", "volume"):
            return False
        try:
            parts = shlex.split(line)
            if self.pending_navigation and (
                    (first == "tp" and (len(parts) < 2 or parts[1].casefold() not in ("list", "status"))) or
                    (first == "track" and (len(parts) < 2 or parts[1].casefold() not in ("list", "status"))) or
                    (first == "event" and len(parts) > 1 and parts[1].casefold() == "start")):
                raise ValueError("Wait for the pending GUI map action to finish.")
            if first == "volume":
                if len(parts) != 2:
                    raise ValueError("usage: volume <0..1> (for example volume 0.4)")
                self.set_volume(parts[1])
            elif first == "event":
                from .gamedata import GameData
                usage = "usage: event list [map] [page] | info <race ID> | start <race ID> | stop | status"
                operation = parts[1].casefold() if len(parts) > 1 else ""
                if operation == "list":
                    if len(parts) > 4:
                        raise ValueError(usage)
                    map_name = resolve_track(parts[2])[0] if len(parts) >= 3 else None
                    page = int(parts[3]) if len(parts) == 4 else 1
                    races = GameData(self.s.get("mount", "")).event_races(self.s.get("language", "EN"))
                    if map_name:
                        races = [race for race in races if race.map_name == map_name]
                    pages = max(1, (len(races) + 24) // 25)
                    if not 1 <= page <= pages:
                        raise ValueError("Event list page must be between 1 and %d." % pages)
                    lines = ["%d | %s | %s | track=%d route=%d laps=%d" %
                             (race.id, race.map_name, race.name, race.track_id, race.route, race.laps)
                             for race in races[(page - 1) * 25:page * 25]]
                    self.console.notice("Standard races (AnthemEventRaceId), page %d/%d (%d total):\n%s" %
                                        (page, pages, len(races), "\n".join(lines)))
                elif operation in ("info", "start") and len(parts) == 3:
                    if not parts[2].isascii() or not parts[2].isdigit() or not 0 < int(parts[2]) <= 2147483647:
                        raise ValueError("Race ID must be a positive integer from event list, not a track ID.")
                    race_id = int(parts[2])
                    races = GameData(self.s.get("mount", "")).event_races(self.s.get("language", "EN"))
                    race = next((race for race in races if race.id == race_id), None)
                    if race is None:
                        raise ValueError("Unsupported standard race ID %d; use event list." % race_id)
                    self.console.notice("%d | %s | %s | track=%d route=%d laps=%d" %
                                        (race.id, race.map_name, race.name, race.track_id, race.route, race.laps))
                    if operation == "start":
                        if not self.rt.running():
                            raise ValueError("Start the wrapper and load %s free roam first." % race.map_name)
                        reply = self.rt.command("CarPackStudio event start %d" % race_id)
                        self.console.notice(reply, theme.RED if reply.startswith("error:") else None)
                elif operation in ("status", "stop") and len(parts) == 2:
                    reply = self.rt.command("CarPackStudio event " + operation)
                    self.console.notice(reply, theme.RED if reply.startswith("error:") else None)
                else:
                    raise ValueError(usage)
            elif first == "track":
                value = " ".join(parts[1:])
                if value.casefold() == "list":
                    self.console.notice("Available tracks:\n" + "\n".join(
                        "track %s (%d)" % (name, track_id) for name, track_id in TRACKS.items()))
                    return True
                if value.casefold() == "status":
                    reply = self.rt.command("CarPackStudio track status")
                else:
                    if not value:
                        raise ValueError("usage: track Anthem | TheIsland | 542 | 549 | list | status")
                    name, track_id = resolve_track(value)
                    if not self.rt.running():
                        raise ValueError("Start the wrapper and enter free roam before changing tracks.")
                    reply = self.rt.command("CarPackStudio track %d" % track_id)
                    if reply.startswith("track queued:"):
                        self.console.notice("Travel to %s requested. Native progress appears below; "
                                            "use track status to inspect it." % name)
                self.console.notice(reply, theme.RED if reply.startswith("error:") else None)
            elif first == "tp":
                if len(parts) == 2 and parts[1].casefold() == "list":
                    self.console.notice("Available teleport entries:\n" + "\n".join(teleport_entries()))
                    return True
                if len(parts) == 2 and parts[1].casefold() == "status":
                    reply = self.rt.command("CarPackStudio teleport status")
                    self.console.notice(reply, theme.RED if reply.startswith("error:") else None)
                    return True
                if len(parts) >= 2 and parts[1].casefold() in ("add", "remove"):
                    self._edit_teleport_place(parts[1].casefold(), " ".join(parts[2:]))
                    return True
                if len(parts) < 2:
                    raise ValueError("usage: tp [map] <place> | tp add <name> | tp remove <name> | tp list | tp status")
                if not self.rt.running():
                    raise ValueError("Start the wrapper and enter free roam before teleporting.")
                if len(parts) >= 3:
                    map_name, track_id = resolve_place_track(parts[1])
                    requested = " ".join(parts[2:])
                else:
                    track_id, _ = parse_player_position(self.rt.command("CarPackStudio teleport capture"))
                    map_name, requested = track_label(track_id), parts[1]
                custom = next((p for p in load_places()
                               if p.track_id == track_id and place_key(p.name) == place_key(requested)), None)
                if custom:
                    name, coordinates = custom.name, custom.position
                else:
                    name, coordinates = resolve_destination(self.s.get("mount", ""), map_name, requested)
                current_id = None
                if self.active_car:
                    try:
                        current_id = self.rt.car_id(self.active_car)
                    except (OSError, sqlite3.Error) as ex:
                        self.console.notice("Cannot resolve car for teleport recovery: %s" % ex, theme.ORANGE)
                reply = self.rt.command("CarPackStudio teleport at %d " % track_id +
                                        " ".join(format(value, ".9g") for value in coordinates))
                if not reply.startswith("tp queued:"):
                    raise ValueError(reply)
                self.console.notice("%s / %s: %s" % (map_name, name, reply))
                if current_id is not None:
                    self._track_reload(current_id, current_id)
                    self.state.setText("Teleporting to %s / %s..." % (map_name, name))
            elif first == "console":
                if len(parts) != 2 or parts[1] not in ("0", "1"):
                    raise ValueError("usage: console 1 (detach) | console 0 (attach)")
                self.set_console_detached(parts[1] == "1")
                self.console.notice("Console " + ("detached" if parts[1] == "1" else "attached"))
            elif first == "filter":
                if len(parts) == 2 and parts[1].lower() == "remove":
                    self.console.set_filters([])
                elif len(parts) == 3 and parts[1].lower() in ("text", "resource", "-text", "-resource") and parts[2]:
                    kind = parts[1].lower()
                    rule = (kind.lstrip("-"), parts[2], kind.startswith("-"))
                    if rule not in self.console.filters:
                        self.console.set_filters(self.console.filters + [rule])
                else:
                    raise ValueError('usage: filter [-]text "match" | filter [-]resource "name" | filter remove')
                self.console.notice("%d active filters" % len(self.console.filters))
            else:
                target = " ".join(parts[1:]).strip()
                if not target:
                    raise ValueError('usage: debug Azimuth | debug "mod name" | debug off')
                if target.lower() == "off":
                    if self.rt.running():
                        reply = self.rt.command("CarPackStudio pipelines off")
                        if reply.startswith("error:"):
                            raise ValueError(reply)
                    self.debug_targets.clear()
                    self.console.notice("Debug log streaming disabled.")
                else:
                    session = self.rt.session() or {}
                    names = {name.casefold(): name for name in session.get("mods", {})}
                    names.update({"azimuth": "Azimuth", "extraoptions": "ExtraOptions"})
                    if target.casefold() not in names:
                        raise ValueError("Unknown log source '%s'. Available: %s" % (target, ", ".join(sorted(names.values()))))
                    if target.casefold() == "azimuth":
                        if not self.rt.running():
                            raise ValueError("Start the wrapper first to enable native Azimuth pipeline tracing.")
                        reply = self.rt.command("CarPackStudio pipelines on")
                        if not reply.startswith("Azimuth "):
                            raise ValueError(reply)
                        self.console.notice(reply)
                    self.debug_targets.add(target.casefold())
                    self.console.notice("Debug %s: streaming selected sources plus errors and critical messages. "
                                        "%s Console filters still apply." % (
                                            names[target.casefold()],
                                            "Native D3D12 pipeline creation/cache calls include thread IDs, durations and HRESULTs."
                                            if target.casefold() == "azimuth" else
                                            "Mod details depend on the mod's own logging."))
        except (ValueError, OSError, ET.ParseError, KeyError, sqlite3.Error, zipfile.BadZipFile) as ex:
            self.console.notice("error: %s" % ex, theme.RED)
        return True

    def _edit_teleport_place(self, operation, value):
        name = validate_place_name(value)
        if operation == "add":
            self.add_current_place(name)
        else:
            matches = [p for p in load_places() if place_key(p.name) == place_key(name)]
            if not matches:
                raise ValueError('Custom place "%s" does not exist; built-in places cannot be removed.' % name)
            if len(matches) > 1:
                track_id, _ = parse_player_position(self.rt.command("CarPackStudio teleport capture"))
                matches = [p for p in matches if p.track_id == track_id]
                if len(matches) != 1:
                    raise ValueError("That name exists on several maps; load its map before removing it.")
            self.delete_place(matches[0].track_id, matches[0].name)

    def teleport_to(self, track_id, name):
        self.navigate(track_id, 'tp %d "%s"' % (track_id, name))

    def add_current_place(self, name):
        name = validate_place_name(name)
        if not self.rt.running():
            raise ValueError("Start the wrapper and enter settled free roam before adding a place.")
        if self.pending_navigation:
            raise ValueError("Wait for the pending map action before saving a place.")
        track_id, position = parse_player_position(self.rt.command("CarPackStudio teleport capture"))
        place = add_place(TeleportPlace(name, track_id, position))
        self.console.notice('Saved custom place "%s" on %s.' % (place.name, track_label(place.track_id)))
        self.places_changed.emit()
        return place

    def delete_place(self, track_id, name):
        place = remove_place(track_id, name)
        self.console.notice('Removed custom place "%s" on %s.' % (place.name, track_label(place.track_id)))
        self.places_changed.emit()

    def show_places(self):
        if self.places_window is None:
            from .travel_ui import PlacesDialog
            self.places_window = PlacesDialog(self)
        self.places_window.refresh()
        self.places_window.show()
        self.places_window.raise_()
        self.places_window.activateWindow()

    def show_scripts(self):
        if self.scripts_window is None:
            from .scripts import ScriptsDialog
            self.scripts_window = ScriptsDialog(self)
        self.scripts_window.show()
        self.scripts_window.raise_()
        self.scripts_window.activateWindow()

    def start_map_capture(self):
        self.send("trace start")

    def export_map_capture(self):
        if self.rt.running():
            reply = self.rt.command("CarPackStudio trace stop")
            self.console.notice(reply)
            if not reply.startswith("trace stopped:"):
                return
        source = Path(self.rt.mods_dir) / "CPSMapTrace.jsonl"
        if not source.is_file():
            self.console.notice("error: no map transition capture; start capture before changing maps in game.", theme.RED)
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export map transition capture", "map-transition.jsonl",
                                             "JSON Lines (*.jsonl)")
        if path:
            try:
                shutil.copyfile(source, path)
                self.console.notice("Map transition capture exported: " + path)
            except OSError as ex:
                self.console.notice("error: cannot export capture: %s" % ex, theme.RED)

    def _critical_exit(self, code):
        msg = "CRITICAL: game process exited with code 0x%08X" % (code & 0xFFFFFFFF)
        self.console.write(msg, theme.RED)
        try:
            with open(os.path.join(self.rt.mods_dir, "CarPackStudio-critical.log"), "a", encoding="utf-8") as f:
                f.write("[%s] %s (see fh2-mods\\crashes and data\\logs)\r\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg))
        except OSError:
            pass

    def on_command(self, line):
        line = line.strip()
        if not line:
            return
        first = line.split(None, 1)[0].lower()
        if first == "script":
            self._command_script_command(line)
            return
        if self.command_recorder.recording and self.command_playback is None:
            try:
                self.command_recorder.add(line)
            except ValueError as ex:
                self.console.notice("error: %s" % ex, theme.RED)
        if first == "wrapper" or first in ("screenshot", "quit"):
            self._utility_command(line)
            return
        if line in ("help", "?"):
            self.console.notice("Commands go to the FH2 Mod Platform as '<mod> <command>'. Studio shortcuts: "
                               "time <h>, freeze <0|1>, weather <atm> <rain> <env>|off, status, recover <0|1>, "
                               "car recover (restart in previous car), car force (restart in swap target), "
                               "car <id>, car status, car details, echo <text>. Up/Down recalls session commands. "
                               "Tab completes the line end; repeated Tab cycles matches, Shift+Tab cycles backwards. "
                               'filter [-]text "match", filter [-]resource "name", filter remove; '
                               'debug Azimuth|"<mod name>"|off; console 1 (detach), console 0 (attach). '
                               "volume <0..1> (wrapper process only, decimals supported); "
                               "track Anthem|TheIsland|542|549, track list, track status; "
                               "tp [map] <place>, tp add|remove <name>, tp list, tp status; "
                               "event list [map] [page], event info|start <race ID>, event stop|status (experimental); "
                               "spawn car|ai|drivatar <carID>, spawn list [page], spawn status, "
                               "spawn remove car <object ID>, spawn clear "
                               "(experimental: four cars total, load one car at a time; AI starts paused; clear before travel/events/car reload); "
                               "drivatar pause|play|reset [object ID] (Studio AI only; follow/chase unavailable); "
                               "spawn swap <object ID>|reset|status (driverless cars only; controls and camera); "
                               "drivatar <object ID> swap, drivatar swap reset (same temporary possession); "
                               "trace start|stop|status; input record <0-3>|play|stop|status; "
                               "wrapper play|stop; screenshot; quit; "
                               "script record [name], script play <name>, script list, script stop. "
                               "'list' shows everything.", theme.FG_DIM)
            return
        self.send(line, echo=False)

    def _utility_command(self, line):
        try:
            parts = shlex.split(line)
            first = parts[0].lower()
            if first == "wrapper":
                if len(parts) != 2 or parts[1].lower() not in ("play", "stop"):
                    raise ValueError("usage: wrapper play | wrapper stop")
                if parts[1].lower() == "play":
                    self.start()
                else:
                    self.stop()
            elif first == "screenshot":
                if len(parts) != 1:
                    raise ValueError("usage: screenshot")
                self.take_screenshot()
            else:
                if len(parts) != 1:
                    raise ValueError("usage: quit")
                window = self.window()
                if window is not None:
                    window.close()
                else:
                    QApplication.quit()
        except ValueError as ex:
            self.console.notice("error: %s" % ex, theme.RED)

    def _command_script_command(self, line):
        try:
            parts = shlex.split(line)
            if len(parts) < 2:
                raise ValueError("usage: script record [name] | script play <name> | script list | script stop")
            operation = parts[1].lower()
            if operation == "record":
                if len(parts) > 3:
                    raise ValueError("usage: script record [name]")
                if self.command_playback is not None or self.command_recorder.recording:
                    raise ValueError("Stop the current command script before starting another.")
                name = parts[2] if len(parts) == 3 else time.strftime("commands_%Y%m%d_%H%M%S")
                self.command_recorder.start(name)
                self.console.notice('Recording commands to "%s". Use "script stop" to save.' % name)
            elif operation == "play":
                if len(parts) != 3:
                    raise ValueError("usage: script play <name>")
                if self.command_recorder.recording or self.command_playback is not None:
                    raise ValueError("Stop the current command script before playback.")
                actions = self.command_scripts.load(parts[2])
                self.command_playback = actions
                self.command_playback_index = 0
                self.command_playback_started = time.monotonic()
                self.command_script_timer.start()
                self.console.notice('Playing command script "%s" (%d actions).' % (parts[2], len(actions)))
                if not actions:
                    self._finish_command_playback()
            elif operation == "list" and len(parts) == 2:
                names = self.command_scripts.names()
                self.console.notice("Command scripts:\n" + ("\n".join(names) if names else "(none)"))
            elif operation == "stop" and len(parts) == 2:
                self._stop_command_scripts()
            else:
                raise ValueError("usage: script record [name] | script play <name> | script list | script stop")
        except (OSError, UnicodeError, ValueError) as ex:
            self.console.notice("error: %s" % ex, theme.RED)

    def _advance_command_script(self):
        actions = self.command_playback
        if actions is None:
            self.command_script_timer.stop()
            return
        elapsed = int((time.monotonic() - self.command_playback_started) * 1000)
        dispatched = 0
        while (self.command_playback_index < len(actions) and
               actions[self.command_playback_index]["at_ms"] <= elapsed and dispatched < 20):
            command = actions[self.command_playback_index]["command"]
            self.command_playback_index += 1
            dispatched += 1
            self.on_command(command)
            if self.command_playback is None:
                return
        if self.command_playback_index >= len(actions):
            self._finish_command_playback()

    def _finish_command_playback(self):
        self.command_script_timer.stop()
        self.command_playback = None
        self.command_playback_index = 0
        self.console.notice("Command script playback finished.")

    def _stop_command_scripts(self):
        stopped = False
        if self.command_recorder.recording:
            path = self.command_recorder.stop()
            self.console.notice("Command script saved: %s" % path, theme.GREEN)
            stopped = True
        if self.command_playback is not None:
            self.command_script_timer.stop()
            self.command_playback = None
            self.command_playback_index = 0
            self.console.notice("Command script playback stopped.")
            stopped = True
        if not stopped:
            self.console.notice("No command script is recording or playing.")

    def send(self, line, echo=True):
        line = line.strip()
        if self._console_command(line):
            return
        first = line.split(None, 1)[0].lower() if line else ""
        if first == "drivatar":
            line = first + line[len(first):]
        if first in STUDIO_COMMANDS:
            line = "CarPackStudio " + line
        if echo:
            self.console.notice("> " + line, theme.ACCENT)
        parts = line.split()
        command = [part.lower() for part in parts]
        if command in (["carpackstudio", "car", "recover"], ["carpackstudio", "car", "force"]):
            self._restart_swap_car(command[-1] == "force")
            return
        is_swap = (len(parts) == 3 and parts[0].lower() == "carpackstudio" and
                   parts[1].lower() == "car" and parts[2].isascii() and parts[2].isdigit())
        previous_id = None
        if is_swap and self.active_car:
            try:
                previous_id = self.rt.car_id(self.active_car)
            except (OSError, sqlite3.Error) as ex:
                self.console.write("Cannot resolve previous car for recovery: %s" % ex, theme.RED)
        reply = self.rt.command(line)
        self.console.notice(reply, theme.RED if reply.startswith("error:") else None)
        if len(command) >= 2 and command[0] == "carpackstudio" and command[1] in ("spawn", "drivatar"):
            self.poller.refresh_native = True
        if is_swap and reply.startswith("swapping to car ") and reply[16:].isdigit():
            self._track_reload(int(reply[16:]), previous_id)

    def _track_reload(self, target, previous_id):
        self.swap_target = target
        self.swap_previous_id = previous_id
        self.swap_started = time.monotonic()
        self.swap_timed_out = False
        self.recover_btn.hide()
        self.poller.swap_target = target
        self.state.setText("Swapping to ID %d..." % target)
        if previous_id is None:
            self.console.write("Previous car ID unavailable; timeout recovery will require Stop/Run.", theme.ORANGE)

    def _clear_swap(self):
        self.swap_target = None
        self.swap_previous_id = None
        self.swap_timed_out = False
        self.poller.swap_target = None
        self.recover_btn.hide()

    def _check_swap_timeout(self):
        if self.swap_target is None or self.swap_timed_out or time.monotonic() - self.swap_started < 60:
            return
        self.swap_timed_out = True
        self.state.setText("Swap to ID %d timed out" % self.swap_target)
        self.recover_btn.setVisible(self.swap_previous_id is not None)
        self.console.write("ERROR: swap to ID %d has not completed after 60 seconds. "
                           "Use 'car details' to inspect the native stage. %s" %
                           (self.swap_target, "Recover previous car restarts the wrapper at the normal spawn point."
                            if self.swap_previous_id is not None else "Stop/Run the wrapper to recover."), theme.RED)

    def on_swap_status(self, result):
        target, reply = result
        if target != self.swap_target:
            return
        if reply == "complete %d" % target:
            self.console.write("Swap to ID %d completed." % target, theme.GREEN)
            self._clear_swap()
        elif reply == "failed %d" % target:
            self.console.write("ERROR: swap to ID %d failed. Use 'car details' for diagnostics." % target, theme.RED)
            self.poller.swap_target = None
            self.swap_timed_out = True
            self.recover_btn.setVisible(self.swap_previous_id is not None)
            self.state.setText("Car swap failed")

    def recover_swap(self):
        if not self.swap_timed_out or self.swap_previous_id is None:
            self.console.write("Recovery unavailable: no timed-out swap with a known previous car.", theme.RED)
            return
        self._restart_swap_car(False)

    def _restart_swap_car(self, use_target):
        if self.swap_target is None or not self.rt.running():
            self.console.write("error: recovery requires an active car swap in a running wrapper.", theme.RED)
            return
        car_id = self.swap_target if use_target else self.swap_previous_id
        if car_id is None:
            self.console.write("error: previous car ID is unknown; use 'car force' to restart in the target "
                               "car, or Stop/Run.", theme.RED)
            return
        self.console.write("Recovering: restarting in %s car ID %d at the normal spawn point; "
                           "the incomplete native load will not be forced to finish." %
                           ("target" if use_target else "previous", car_id), theme.ORANGE)
        self.forced_car = car_id
        self.rt.last_car = car_id
        self.restart()

    def restart(self):
        """Switch car: the next launch starts in rt.last_car (only the save's LastUsedGarageId changes)."""
        self.console.write("Restarting the game in %s …" % (self.car or "the selected car"), theme.FG_DIM)
        after, self.after_cb = self.after_cb, None
        self.stop()
        self.after_cb = after
        QTimer.singleShot(1500, self.start)

    def shutdown(self):
        if self.command_recorder.recording:
            try:
                path = self.command_recorder.stop()
                self.console.notice("Command script saved during shutdown: %s" % path)
            except OSError as ex:
                self.console.notice("Cannot save command script during shutdown: %s" % ex, theme.RED)
        self.command_script_timer.stop()
        self.command_playback = None
        if self.scripts_window and (self.scripts_window.capturing or self.scripts_window.playing) and self.rt.running():
            self.scripts_window.stop()
        self.set_console_detached(False)
        self.poller.alive = False
        if self.rt.running() and self.embedded:
            self.container.release(self.rt)
        self.rt.stop()
        self.poller.wait()
        if self.scripts_window:
            self.scripts_window.timer.stop()
            self.scripts_window.close()
        if self.places_window:
            self.places_window.close()
