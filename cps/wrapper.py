"""Wrapper tab: embeds the running game, telemetry overlay and console."""
import os
import time

from PySide6.QtCore import Qt, QTimer, QThread, Signal, QRectF
from PySide6.QtGui import QColor, QPainter, QPen, QFont, QTextCursor
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QPlainTextEdit,
                               QLineEdit, QSplitter, QCheckBox, QStackedLayout, QApplication)

from . import theme
from .overlay import TelemetryOverlay
from .runtime import Runtime, user32, WS_CHILD, WS_VISIBLE, GWL_STYLE, send_key

VK_RETURN = 0x0D
STUDIO_COMMANDS = {"time", "freeze", "weather", "status", "recover", "echo", "car"}
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

    def __init__(self, rt):
        super().__init__()
        self.rt = rt
        self.alive = True

    def run(self):
        while self.alive:
            self.data.emit(self.rt.telemetry() if self.rt.running() else None)
            time.sleep(0.1)


class Console(QWidget):
    submit = Signal(str)

    def __init__(self):
        super().__init__()
        self.out = QPlainTextEdit()
        self.out.setReadOnly(True)
        self.out.setMaximumBlockCount(5000)
        self.inp = QLineEdit()
        self.inp.setPlaceholderText("Console – type a mod command (try: list) and press Enter")
        self.inp.returnPressed.connect(self._enter)
        self.hist, self.hi = [], 0
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(self.out)
        lay.addWidget(self.inp)

    def write(self, text, color=None):
        c = self.out.textCursor()
        c.movePosition(QTextCursor.End)
        self.out.setTextCursor(c)
        html = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        self.out.appendHtml('<span style="color:%s;white-space:pre">%s</span>' % (color or theme.FG, html))

    def _enter(self):
        t = self.inp.text().strip()
        if t:
            self.hist.append(t)
            self.hi = len(self.hist)
            self.write("> " + t, theme.ACCENT)
            self.submit.emit(t)
        self.inp.clear()

    def keyPressEvent(self, e):
        super().keyPressEvent(e)


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
        self.embedded = False
        self.detached = False
        self.launch_t = 0
        self.last_enter = 0
        self.log_pos = {}
        self.driving = False

        self.run_btn = QPushButton("▶ Run")
        self.run_btn.setObjectName("primary")
        self.run_btn.clicked.connect(self.start)
        self.stop_btn = QPushButton("■ Stop")
        self.stop_btn.clicked.connect(self.stop)
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
        for w in (self.run_btn, self.stop_btn, self.detach_btn, self.boot, self.show_overlay, self.show_fps,
                  self.car_lbl):
            bar.addWidget(w)
        bar.addStretch()
        bar.addWidget(self.state)

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
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addLayout(bar)
        lay.addWidget(split)

        self.poller = Poller(self.rt)
        self.poller.data.connect(self.on_telemetry)
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
            self.rt.launch()
        except Exception as ex:
            self.console.write("Cannot start: %s" % ex, theme.RED)
            return
        self.launch_t = time.time()
        self.embedded = False
        self.detached = False
        self.driving = False
        self.state.setText("Starting…")
        self.console.write("azimuth.exe started (pid %d)" % self.rt.proc.pid, theme.GREEN)

    def stop(self):
        if self.embedded:
            self.container.child = None
        self.rt.stop()
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
        if running:
            self._pump_logs()
        self.overlay.follow(self.embedded and self.show_overlay.isChecked())

    def on_telemetry(self, t):
        self.overlay.update_data(t)
        self.overlay.follow(self.embedded and self.show_overlay.isChecked())
        if t and t.get("valid") and not self.driving:
            self.driving = True
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
            if size > pos:
                with open(path, "rb") as f:
                    f.seek(pos)
                    chunk = f.read(min(size - pos, 65536)).decode("utf-8", "replace")
                self.log_pos[key] = pos + min(size - pos, 65536)
                for line in chunk.splitlines():
                    if key == "azimuth" and "local save" in line:
                        continue
                    if key == "critical":
                        self.console.write(line, theme.RED)
                    elif key == "extraoptions":
                        bad = any(w in line.lower() for w in ("refused", "error", "fail", "cannot", "invalid"))
                        self.console.write("[ExtraOptions] " + line, theme.RED if bad else theme.FG_DIM)
                    elif any(w in line.lower() for w in ("error", "fail", "crash")):
                        self.console.write("[%s] %s" % (key, line), theme.ORANGE)
                    elif key == "platform":
                        self.console.write(line, theme.FG_DIM)

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
            self.console.write("Commands go to the FH2 Mod Platform as '<mod> <command>'. Studio shortcuts: "
                               "time <h>, freeze <0|1>, weather <atm> <rain> <env>|off, status, recover <0|1>, "
                               "echo <text>. 'list' shows everything.", theme.FG_DIM)
            return
        self.send(line, echo=False)

    def send(self, line, echo=True):
        first = line.split(" ", 1)[0].lower()
        if first in STUDIO_COMMANDS:
            line = "CarPackStudio " + line
        if echo:
            self.console.write("> " + line, theme.ACCENT)
        self.console.write(self.rt.command(line))

    def restart(self):
        """Switch car: the next launch starts in rt.last_car (only the save's LastUsedGarageId changes)."""
        self.console.write("Restarting the game in %s …" % (self.car or "the selected car"), theme.FG_DIM)
        after, self.after_cb = self.after_cb, None
        self.stop()
        self.after_cb = after
        QTimer.singleShot(1500, self.start)

    def shutdown(self):
        self.poller.alive = False
        self.poller.wait(1000)
        if self.rt.running() and self.embedded:
            self.container.release(self.rt)
        self.rt.stop()
