"""Portable XInput recordings and their native WGI transport."""
import json
import math
import os
import re
import struct
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
                               QSpinBox, QFileDialog, QMessageBox)

FRAME = struct.Struct("<II6d")
MAX_FRAMES = 100000
MAX_MS = 1800000
BUTTONS = (0x10, 0x20, 0x1000, 0x2000, 0x4000, 0x8000, 1, 2, 4, 8, 0x100, 0x200, 0x40, 0x80)
FIELDS = ("lt", "rt", "lx", "ly", "rx", "ry")


def validate_script(value):
    if (not isinstance(value, dict) or value.get("format") != "CPS-XInput" or
            type(value.get("version")) is not int or value.get("version") != 1):
        raise ValueError("Expected a CPS-XInput version 1 script")
    frames = value.get("frames")
    if not isinstance(frames, list) or not 2 <= len(frames) <= MAX_FRAMES:
        raise ValueError("A script needs 2 to 100,000 frames")
    previous = 0
    for frame in frames:
        if not isinstance(frame, dict):
            raise ValueError("Each frame must be an object")
        ms, buttons = frame.get("ms"), frame.get("buttons")
        if type(ms) is not int or not previous <= ms <= MAX_MS:
            raise ValueError("Frame times must be increasing milliseconds, maximum 30 minutes")
        if type(buttons) is not int or buttons < 0 or buttons & ~sum(BUTTONS):
            raise ValueError("Invalid XInput button mask")
        for field in FIELDS:
            number = frame.get(field)
            lower = 0 if field in ("lt", "rt") else -1
            if (not isinstance(number, (float, int)) or isinstance(number, bool) or
                    not lower <= number <= 1 or not math.isfinite(number)):
                raise ValueError("Invalid normalized XInput value: %s" % field)
        previous = ms
    if frames[0]["ms"] != 0 or not frames[-1]["ms"]:
        raise ValueError("A script must start at zero and have positive duration")
    if frames[-1]["buttons"] or any(frames[-1][field] for field in FIELDS):
        raise ValueError("A script must finish with all controls released")
    return value


def decode_recording(data):
    if len(data) < 12:
        raise ValueError("Truncated input recording")
    magic, version, count = struct.unpack_from("<4sII", data)
    if magic != b"CPSI" or version != 1 or not 2 <= count <= MAX_FRAMES or len(data) != 12 + count * FRAME.size:
        raise ValueError("Invalid input recording header or length")
    frames = []
    for offset in range(12, len(data), FRAME.size):
        ms, wgi, *axes = FRAME.unpack_from(data, offset)
        if wgi & ~0x3fff:
            raise ValueError("Unsupported Windows Gaming Input button mask")
        buttons = sum(mask for bit, mask in enumerate(BUTTONS) if wgi & (1 << bit))
        frames.append(dict(zip(("ms", "buttons", *FIELDS), (ms, buttons, *axes))))
    return validate_script({"format": "CPS-XInput", "version": 1, "frames": frames})


def encode_recording(script):
    validate_script(script)
    frames = script["frames"]
    output = bytearray(struct.pack("<4sII", b"CPSI", 1, len(frames)))
    for frame in frames:
        wgi = sum(1 << bit for bit, mask in enumerate(BUTTONS) if frame["buttons"] & mask)
        output.extend(FRAME.pack(frame["ms"], wgi, *(frame[field] for field in FIELDS)))
    return bytes(output)


class ScriptsDialog(QDialog):
    def __init__(self, wrapper):
        super().__init__(wrapper.window())
        self.wrapper = wrapper
        self.script = None
        self.capturing = False
        self.playing = False
        self.setWindowTitle("XInput scripts")
        self.resize(720, 340)
        layout = QVBoxLayout(self)
        intro = QLabel("Record a connected XInput controller, then replay its buttons, triggers and sticks.\n"
                       "Keep the controller connected for playback. Stop releases all simulated controls.\n"
                       "Playback controls the game; it does not pause automatically for loading screens.")
        intro.setWordWrap(True)
        layout.addWidget(intro)
        row = QHBoxLayout()
        row.addWidget(QLabel("Controller index:"))
        self.slot = QSpinBox()
        self.slot.setRange(0, 3)
        row.addWidget(self.slot)
        for text, callback in (("Record", self.record), ("Stop", self.stop),
                               ("Play", self.play), ("Load JSON...", self.load), ("Save JSON...", self.save)):
            button = QPushButton(text)
            button.clicked.connect(callback)
            row.addWidget(button)
        layout.addLayout(row)
        self.summary = QLabel("No script loaded.")
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        self.state = QLabel("Idle")
        self.state.setWordWrap(True)
        layout.addWidget(self.state)
        layout.addStretch()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.poll)
        self.timer.start(1000)

    def _command(self, args):
        if not self.wrapper.rt.running():
            raise ValueError("Start the wrapper first.")
        reply = self.wrapper.rt.command("CarPackStudio input " + args)
        if reply.startswith("error:") or not reply.startswith("input "):
            raise ValueError(reply)
        self.state.setText(reply)
        return reply

    def _error(self, error):
        QMessageBox.warning(self, "XInput scripts", str(error))

    def _summary(self):
        if self.script:
            self.summary.setText("%d state changes, %.3f seconds. Button masks use standard XInput bits; "
                                 "axes are normalized." % (len(self.script["frames"]), self.script["frames"][-1]["ms"] / 1000))

    def record(self):
        try:
            self._command("record %d" % self.slot.value())
            self.capturing = True
        except ValueError as ex:
            self._error(ex)

    def stop(self):
        try:
            reply = self._command("stop")
            self.capturing = self.playing = False
            if reply.startswith("input recording saved:"):
                self._read_native()
            return True
        except (ValueError, OSError) as ex:
            self._error(ex)
            return False

    def _read_native(self):
        path = Path(self.wrapper.rt.mods_dir) / "CPSInput.bin"
        if path.stat().st_size > 12 + MAX_FRAMES * FRAME.size:
            raise ValueError("Input recording exceeds the size limit")
        self.script = decode_recording(path.read_bytes())
        self._summary()

    def play(self):
        try:
            if self.capturing or self.playing:
                raise ValueError("Stop the current recording/playback first.")
            if self.script is None:
                raise ValueError("Record or load a script first.")
            status = self._command("status")
            if "mode=idle" not in status:
                raise ValueError("Native controller capture/playback is busy; stop it first.")
            path = Path(self.wrapper.rt.mods_dir) / "CPSInput.bin"
            temporary = path.with_suffix(".bin.tmp")
            try:
                temporary.write_bytes(encode_recording(self.script))
                os.replace(temporary, path)
            finally:
                temporary.unlink(missing_ok=True)
            self._command("play")
            self.playing = True
        except (ValueError, OSError) as ex:
            self._error(ex)

    def load(self):
        path, _ = QFileDialog.getOpenFileName(self, "Load XInput script", "", "CPS XInput (*.json)")
        if not path:
            return
        try:
            if Path(path).stat().st_size > 64 * 1024 * 1024:
                raise ValueError("Script exceeds 64 MiB")
            self.script = validate_script(json.loads(Path(path).read_text(encoding="utf-8")))
            self._summary()
        except (ValueError, OSError) as ex:
            self._error(ex)

    def save(self):
        if self.script is None:
            self._error("Record or load a script first.")
            return
        path, _ = QFileDialog.getSaveFileName(self, "Save XInput script", "controller.json", "CPS XInput (*.json)")
        if path:
            try:
                Path(path).write_text(json.dumps(self.script, indent=2, allow_nan=False) + "\n", encoding="utf-8")
            except OSError as ex:
                self._error(ex)

    def poll(self):
        if not self.isVisible():
            return
        if not self.wrapper.rt.running():
            self.capturing = self.playing = False
            self.state.setText("Wrapper stopped; loaded script is retained.")
            return
        try:
            reply = self._command("status")
            if "mode=idle" in reply:
                was_recording = self.capturing
                self.capturing = self.playing = False
                count = re.search(r"\bframes=(\d+)\b", reply)
                if was_recording and count and int(count[1]) >= 2:
                    self._read_native()
        except (ValueError, OSError) as ex:
            self.state.setText(str(ex))

    def closeEvent(self, event):
        if (self.capturing or self.playing) and self.wrapper.rt.running():
            if not self.stop():
                event.ignore()
                return
        super().closeEvent(event)
