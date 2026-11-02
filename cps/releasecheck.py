"""Offline packaged-release verification; never starts the game or changes settings."""
import json
import os
import struct
import subprocess
import sys
import traceback
from pathlib import Path


def verify(report_path):
    report = {"version": "0.1.0", "experimental": True, "checks": []}
    try:
        from PySide6.QtGui import QIcon
        from PySide6.QtWidgets import QApplication, QDialog
        from OpenGL import GL, GLU
        from . import config, theme
        from .audio import ffmpeg_path
        from .audioviewer import BankViewer
        from .filetools import GameFilesManager, BinaryViewer
        from .save import SAVE_FILES, ProfileData
        from .swatch import _load_xg
        import texture2ddecoder

        app = QApplication.instance() or QApplication([])
        theme.apply(app)
        root = Path(config.ROOT)
        required = ["resources\\app.ico", "resources\\how-to.html", "bin\\ffmpeg.exe", "bin\\xg.dll",
                    "runtime\\azimuth.exe", "runtime\\DWrite.dll", "runtime\\winmm.dll",
                    "runtime\\FH2_ExtraOptions.dat", "runtime\\data\\bin\\azimuth.pipelines",
                    "runtime\\fh2-mods\\CarPackLoader.dll", "runtime\\fh2-mods\\CarPackStudio.dll",
                    "runtime\\fh2-mods\\VirtualDashboard.dll"] + ["save_template\\data\\" + name for name in SAVE_FILES]
        for relative in required:
            path = root / relative
            if not path.is_file() or path.stat().st_size == 0:
                raise ValueError("Missing packaged resource: " + relative)
        report["checks"].append("Runtime, native mods, save template and resources included")
        icon = QIcon(str(root / "resources" / "app.ico"))
        if icon.isNull():
            raise ValueError("Packaged application icon cannot be loaded")
        app.setWindowIcon(icon)
        dialog = QDialog()
        if dialog.windowIcon().isNull():
            raise ValueError("Popup icon inheritance failed")
        report["checks"].append("Application and popup icons load")
        ProfileData((root / "save_template" / "data" / "ProfileData").read_bytes())
        report["checks"].append("Save template profile parses")
        _load_xg()
        if len(texture2ddecoder.decode_bc1(bytes(8), 4, 4)) != 64:
            raise ValueError("Bundled texture decoder returned invalid pixels")
        report["checks"].append("Native texture decoder loads and BC1 decodes")
        if GL.glGetString is None or GLU.gluPerspective is None:
            raise ValueError("OpenGL bindings unavailable")
        report["checks"].append("OpenGL/GLU viewer bindings import")
        manager = GameFilesManager(str(root / "runtime"))
        binary = BinaryViewer("physicsdefinition.bin", bytes(64))
        app.processEvents()
        report["checks"].append("Game files manager and binary inspection widgets initialize")
        names = struct.pack("<I", 4) + b"test\0"
        header = bytearray(60)
        header[:4] = b"FSB5"
        struct.pack_into("<6I", header, 4, 1, 1, 8, len(names), 6, 2)
        fsb = bytes(header) + struct.pack("<Q", (9 << 1) | (3 << 34)) + names + bytes(6)
        bank = BankViewer("test.bank", fsb)
        bank.index = 0
        bank.audio.setMuted(True)
        bank.request("play")
        if bank.ready != 0 or not os.path.isfile(bank._wav_path()):
            raise ValueError("Packaged audio-bank preview did not decode PCM")
        bank.stop()
        bank.shutdown()
        report["checks"].append("Audio bank UI, Qt multimedia and PCM-to-WAV decode initialize")
        result = subprocess.run([ffmpeg_path(), "-version"], capture_output=True, timeout=15,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if result.returncode:
            raise ValueError("Bundled FFmpeg failed to run")
        report["checks"].append(result.stdout.decode("utf-8", "replace").splitlines()[0])
        manager.deleteLater()
        binary.deleteLater()
        bank.deleteLater()
        dialog.deleteLater()
        report["ok"] = True
        report["resource_root"] = str(root)
    except Exception:
        report["ok"] = False
        report["error"] = traceback.format_exc()
    Path(report_path).write_text(json.dumps(report, indent=2), encoding="utf-8")
    return 0 if report["ok"] else 1
