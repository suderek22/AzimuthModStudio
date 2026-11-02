# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
from PyInstaller.utils.hooks import copy_metadata

root = Path(SPECPATH)
a = Analysis(
    [str(root / "run.py")],
    pathex=[str(root)],
    binaries=[],
    datas=sum((copy_metadata(package) for package in
               ("PySide6", "shiboken6", "numpy", "Pillow", "PyOpenGL", "texture2ddecoder")), []),
    hiddenimports=["OpenGL.GL", "OpenGL.GLU", "OpenGL.platform.win32", "texture2ddecoder"],
    hookspath=[],
    excludes=["tkinter", "matplotlib", "pandas", "scipy", "IPython", "pytest", "PyQt5", "PyQt6"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [], exclude_binaries=True, name="Car Pack Studio",
    debug=False, strip=False, upx=False, console=False,
    icon=str(root / "resources" / "app.ico"), version=str(root / "version_info.txt"),
)
coll = COLLECT(
    exe, a.binaries, a.datas,
    Tree(str(root / "runtime"), prefix="runtime",
         excludes=["logs", "cache", "platform-logs", "platform.log", "platform-session.json", "*.check"]),
    Tree(str(root / "save_template"), prefix="save_template"),
    Tree(str(root / "resources"), prefix="resources"),
    [("bin\\" + name, str(root / "bin" / name), "DATA") for name in
     ("xg.dll", "ffmpeg.exe", "ffmpeg-license.txt", "ffmpeg-notice.txt")],
    [("README.txt", str(root / "README.txt"), "DATA"),
     ("THIRD-PARTY-NOTICES.txt", str(root / "THIRD-PARTY-NOTICES.txt"), "DATA")],
    strip=False, upx=False, name="CarPackStudio-0.1.0-experimental",
)
