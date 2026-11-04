"""Offline packaged-release verification; never starts the game or changes settings."""
import json
import os
import struct
import subprocess
import sys
import traceback
import tempfile
import ctypes
from pathlib import Path


def _file_version(path):
    api = ctypes.WinDLL("version", use_last_error=True)
    api.GetFileVersionInfoSizeW.argtypes = [ctypes.c_wchar_p, ctypes.c_void_p]
    api.GetFileVersionInfoSizeW.restype = ctypes.c_uint32
    api.GetFileVersionInfoW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p]
    api.VerQueryValueW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p,
                                  ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_uint)]
    size = api.GetFileVersionInfoSizeW(str(path), None)
    if not size:
        raise ctypes.WinError(ctypes.get_last_error())
    data = ctypes.create_string_buffer(size)
    if not api.GetFileVersionInfoW(str(path), 0, size, data):
        raise ctypes.WinError(ctypes.get_last_error())
    value, length = ctypes.c_void_p(), ctypes.c_uint()
    if not api.VerQueryValueW(data, "\\", ctypes.byref(value), ctypes.byref(length)):
        raise ctypes.WinError(ctypes.get_last_error())
    if length.value < 52:
        raise ValueError("Incomplete file version resource: " + str(path))
    info = ctypes.cast(value, ctypes.POINTER(ctypes.c_uint32))
    if info[0] != 0xFEEF04BD:
        raise ValueError("Invalid file version resource: " + str(path))
    return tuple(part for word in (info[2], info[3]) for part in (word >> 16, word & 0xFFFF))


def verify(report_path):
    from . import APP_VERSION
    report = {"version": APP_VERSION, "experimental": True, "checks": []}
    try:
        from PySide6.QtGui import QIcon, QKeyEvent, QPalette
        from PySide6.QtCore import Qt, QEvent
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
        if app.palette().color(QPalette.Window).name() != theme.BG.lower():
            raise ValueError("The application did not retain the dark theme")
        report["checks"].append("Dark theme applied independently of the Windows theme")
        root = Path(config.ROOT)
        required = ["resources\\app.ico", "resources\\how-to.html", "resources\\detach.svg", "resources\\attach.svg",
                    "bin\\ffmpeg.exe", "bin\\xg.dll",
                    "runtime\\fh2-mods\\CarPackLoader.dll", "runtime\\fh2-mods\\CarPackStudio.dll",
                    "runtime\\fh2-mods\\VirtualDashboard.dll"] + ["save_template\\data\\" + name for name in SAVE_FILES]
        for relative in required:
            path = root / relative
            if not path.is_file() or path.stat().st_size == 0:
                raise ValueError("Missing packaged resource: " + relative)
        from .dependencies import BUNDLED_RUNTIME
        actual_runtime = {str(path.relative_to(root / "runtime")) for path in (root / "runtime").rglob("*")
                          if path.is_file()}
        if actual_runtime != set(BUNDLED_RUNTIME):
            raise ValueError("Unexpected packaged runtime files: " + repr(sorted(actual_runtime - set(BUNDLED_RUNTIME))))
        report["checks"].append("Only Studio native mods and Virtual Dashboard bundled; external dependencies excluded")
        expected_version = tuple(map(int, APP_VERSION.split("-")[0].split("."))) + (0,)
        if _file_version(root / "runtime" / "fh2-mods" / "CarPackStudio.dll") != expected_version:
            raise ValueError("CarPackStudio.dll file version does not match the application")
        report["checks"].append("CarPackStudio.dll numeric file version matches the application")
        icon = QIcon(str(root / "resources" / "app.ico"))
        if icon.isNull():
            raise ValueError("Packaged application icon cannot be loaded")
        app.setWindowIcon(icon)
        dialog = QDialog()
        if dialog.windowIcon().isNull():
            raise ValueError("Popup icon inheritance failed")
        report["checks"].append("Application and popup icons load")
        for name in ("detach.svg", "attach.svg"):
            if QIcon(str(root / "resources" / name)).pixmap(18, 18).isNull():
                raise ValueError("Cannot render control icon: " + name)
        from .app import CREDITS
        credit_tail = CREDITS.split("It contains no exported game pixels.", 1)[1]
        if "https://github.com/suderek22" not in credit_tail or "https://fh2mods.com/" not in credit_tail:
            raise ValueError("Credits attribution links missing")
        report["checks"].append("Square control icons render and shortened credits retain attribution links")
        from .console import Console
        from .app import SettingsDialog
        from .dependencies import require_dependencies
        with tempfile.TemporaryDirectory(prefix="studio-release-check-") as directory:
            try:
                require_dependencies(directory)
            except ValueError as ex:
                if "Missing required wrapper dependencies" not in str(ex):
                    raise
            else:
                raise ValueError("An empty runtime passed dependency validation")
        settings = SettingsDialog(dict(config.DEFAULTS))
        if settings.result_settings()["dependency_source"] != "":
            raise ValueError("Dependency-source settings failed")
        settings.s["ultimate_save"] = False
        if not settings.result_settings()["ultimate_save"]:
            raise ValueError("Settings allowed disabling the shipped sandbox save")
        console = Console()
        console.completion_provider = lambda: ["event start ", "event stop"]
        console.inp.setText("event sto")
        app.sendEvent(console.inp, QKeyEvent(QEvent.KeyPress, Qt.Key_Tab, Qt.NoModifier))
        if console.inp.text() != "event stop" or console.hist:
            raise ValueError("Packaged console Tab completion failed")
        console.deleteLater()
        settings.deleteLater()
        report["checks"].append("External dependency validation, setup settings and console Tab completion initialize")
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
