"""Wrapper tab: embeds the running game, telemetry overlay and console."""
import os
import re
import shlex
import shutil
import sqlite3
import time
import xml.etree.ElementTree as ET
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QThread, Signal, QRectF
from PySide6.QtGui import QColor, QPainter, QPen, QFont
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
                               QSplitter, QCheckBox, QStackedLayout, QApplication, QFileDialog)

from . import theme
from .console import Console, ConsoleWindow
from .overlay import TelemetryOverlay
from .runtime import Runtime, user32, WS_CHILD, WS_VISIBLE, GWL_STYLE, send_key
from .travel import DESTINATIONS, TRACKS, resolve_destination, resolve_track

VK_RETURN = 0x0D
STUDIO_COMMANDS = {"time", "freeze", "weather", "status", "recover", "echo", "car", "animation", "animations",
                   "input", "trace", "pipelines"}
SWP_NOZORDER = 0x4


class GameContainer(QWidget):
    resized = Signal()

    def __init__(self):
        super().__init__()
        self.setAttribute(Qt.WA_NativeWindow, True)
        self.setAttribute(Qt.WA_OpaquePaintEvent, True)
        self.setStyleSheet("background:#000;")
        self.setMinimumSize(320, 180)
        self.child = None

    def embed(self, rt, hwnd):
        rt.strip_frame(hwnd)
        st = user32.GetWindowLongPtrW(hwnd, GWL_STYLE)
        user32.SetParent(hwnd, int(self.winId()))
        user32.SetWindowLongPtrW(hwnd, GWL_STYLE, (st | WS_CHILD | WS_VISIBLE) & ~0x80000000)
        self.child = hwnd
        self.fit()

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

    def __init__(self, rt):
        super().__init__()
        self.rt = rt
        self.alive = True
        self.swap_target = None

    def run(self):
        next_swap_poll = 0
        while self.alive:
            self.data.emit(self.rt.telemetry() if self.rt.running() else None)
            target = self.swap_target
            if target is not None and self.rt.running() and time.monotonic() >= next_swap_poll:
                reply = self.rt.command("CarPackStudio car status")
                self.swap_data.emit((target, reply))
                next_swap_poll = time.monotonic() + 1
            time.sleep(0.1)


class WrapperTab(QWidget):
    status = Signal(str)

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

        self.run_btn = QPushButton("▶ Run")
        self.run_btn.setObjectName("primary")
        self.run_btn.clicked.connect(self.start)
        self.stop_btn = QPushButton("■ Stop")
        self.stop_btn.clicked.connect(self.stop)
        self.recover_btn = QPushButton("Recover previous car")
        self.recover_btn.setToolTip("After a stalled swap, restart in the previous car at the normal spawn point.")
        self.recover_btn.clicked.connect(self.recover_swap)
        self.recover_btn.hide()
        self.detach_btn = QPushButton("⧉ Detach")
        self.detach_btn.clicked.connect(self.toggle_detach)
        self.boot = QCheckBox("Skip title screen")
        self.boot.setChecked(settings.get("boot_assist", True))
        self.show_overlay = QCheckBox("Telemetry overlay")
        self.show_overlay.setChecked(True)
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
        for w in (self.run_btn, self.stop_btn, self.recover_btn, self.detach_btn, self.boot, self.show_overlay, self.show_fps):
            bar.addWidget(w)
        bar.addStretch()
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
        self.poller.start()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(400)
        self._show_placeholder(True)

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
        self.active_car = None
        self.state.setText("Starting…")
        self.console.write("azimuth.exe started (pid %d)" % self.rt.proc.pid, theme.GREEN)

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
            self.detach_btn.setText("⧉ Detach")
        else:
            self.container.release(self.rt)
            self.detached = True
            self.embedded = False
            self._show_placeholder(True)
            self.placeholder.setText("Game is running in a detached window.\nPress ⧉ Attach to bring it back.")
            self.detach_btn.setText("⧉ Attach")

    # ------------------------------------------------------------- periodic
    def tick(self):
        running = self.rt.running()
        if running and not self.embedded and not self.detached:
            hwnd = self.rt.game_window()
            if hwnd:
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
            self._pump_logs()
            self._check_swap_timeout()
        self.overlay.follow(self.embedded and self.show_overlay.isChecked())

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
            if self.swap_target is None:
                self.state.setText("Driving: %s" % media)
        if t and t.get("valid") and not self.driving:
            self.driving = True
            if self.swap_target is None:
                self.state.setText("Driving: %s" % t.get("car", ""))
            self.console.write("in game – telemetry live", theme.GREEN)

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
        if re.search(r"\[CarPackStudio\]\s+command:\s+(?:car|input) status\s*$", line, re.I):
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
            self.console_window.show()
            self.console_window.raise_()
        elif self.console_window is not None:
            window, self.console_window = self.console_window, None
            self.console_split.addWidget(self.console)
            self.console.show()
            self.console_split.setSizes(self.console_sizes)
            window.hide()
            window.deleteLater()

    def _console_command(self, line):
        first = line.split(None, 1)[0].lower() if line else ""
        if first not in ("filter", "debug", "console", "tp", "track"):
            return False
        try:
            parts = shlex.split(line)
            if first == "track":
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
                    self.console.notice("Available teleport entries:\n" +
                                        "\n".join("tp Anthem " + name for name in DESTINATIONS))
                    return True
                if len(parts) == 2 and parts[1].casefold() == "status":
                    reply = self.rt.command("CarPackStudio teleport status")
                    self.console.notice(reply, theme.RED if reply.startswith("error:") else None)
                    return True
                if len(parts) < 3:
                    raise ValueError("usage: tp Anthem <place> | tp list | tp status; e.g. tp Anthem Airport")
                name, coordinates = resolve_destination(self.s.get("mount", ""), parts[1], " ".join(parts[2:]))
                if not self.rt.running():
                    raise ValueError("Start the wrapper and enter free roam before teleporting.")
                current_id = None
                if self.active_car:
                    try:
                        current_id = self.rt.car_id(self.active_car)
                    except (OSError, sqlite3.Error) as ex:
                        self.console.notice("Cannot resolve car for teleport recovery: %s" % ex, theme.ORANGE)
                reply = self.rt.command("CarPackStudio teleport " + " ".join(format(value, ".9g") for value in coordinates))
                if not reply.startswith("tp queued:"):
                    raise ValueError(reply)
                self.console.notice("Anthem / %s: %s" % (name, reply))
                if current_id is not None:
                    self._track_reload(current_id, current_id)
                    self.state.setText("Teleporting to Anthem / %s..." % name)
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
        except (ValueError, OSError, ET.ParseError, KeyError) as ex:
            self.console.notice("error: %s" % ex, theme.RED)
        return True

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
        if line in ("help", "?"):
            self.console.notice("Commands go to the FH2 Mod Platform as '<mod> <command>'. Studio shortcuts: "
                               "time <h>, freeze <0|1>, weather <atm> <rain> <env>|off, status, recover <0|1>, "
                               "car recover (restart in previous car), car force (restart in swap target), "
                               "car <id>, car status, car details, echo <text>. Up/Down recalls session commands. "
                               'filter [-]text "match", filter [-]resource "name", filter remove; '
                               'debug Azimuth|"<mod name>"|off; console 1 (detach), console 0 (attach). '
                               "track Anthem|TheIsland|542|549, track list, track status; "
                               "tp Anthem <place>, tp list, tp status; trace start|stop|status; input record <0-3>|play|stop|status. "
                               "'list' shows everything.", theme.FG_DIM)
            return
        self.send(line, echo=False)

    def send(self, line, echo=True):
        line = line.strip()
        if self._console_command(line):
            return
        first = line.split(None, 1)[0].lower() if line else ""
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
        if self.scripts_window and (self.scripts_window.capturing or self.scripts_window.playing) and self.rt.running():
            self.scripts_window.stop()
        self.set_console_detached(False)
        self.poller.alive = False
        self.poller.wait(1000)
        if self.rt.running() and self.embedded:
            self.container.release(self.rt)
        self.rt.stop()
        if self.scripts_window:
            self.scripts_window.timer.stop()
            self.scripts_window.close()
