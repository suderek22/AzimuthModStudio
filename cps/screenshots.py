"""Capture only the native game client, without resizing or editor chrome."""
import ctypes
import struct
from ctypes import wintypes
from datetime import datetime
from pathlib import Path

from PySide6.QtGui import QImage
from PIL import Image

from . import config
from .runtime import user32


def capture_client(hwnd) -> QImage:
    if not hwnd or not user32.IsWindow(hwnd) or user32.IsIconic(hwnd):
        raise ValueError("The game window must be available and not minimized.")
    rect = wintypes.RECT()
    if not user32.GetClientRect(hwnd, ctypes.byref(rect)):
        raise OSError("Cannot read the game client size.")
    width, height = rect.right, rect.bottom
    if not 1 <= width <= 16384 or not 1 <= height <= 16384:
        raise ValueError("Invalid game client resolution: %d x %d" % (width, height))
    gdi = ctypes.windll.gdi32
    user32.GetDC.argtypes = [wintypes.HWND]
    user32.GetDC.restype = wintypes.HDC
    user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
    user32.PrintWindow.argtypes = [wintypes.HWND, wintypes.HDC, wintypes.UINT]
    user32.PrintWindow.restype = wintypes.BOOL
    gdi.CreateCompatibleDC.argtypes = [wintypes.HDC]
    gdi.CreateCompatibleDC.restype = wintypes.HDC
    gdi.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
    gdi.CreateCompatibleBitmap.restype = wintypes.HBITMAP
    gdi.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
    gdi.SelectObject.restype = wintypes.HGDIOBJ
    gdi.DeleteObject.argtypes = [wintypes.HGDIOBJ]
    gdi.DeleteDC.argtypes = [wintypes.HDC]
    gdi.GetDIBits.argtypes = [wintypes.HDC, wintypes.HBITMAP, wintypes.UINT, wintypes.UINT,
                            ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT]
    screen = user32.GetDC(hwnd)
    memory = bitmap = previous = None
    try:
        if not screen:
            raise OSError("Cannot acquire the game device context.")
        memory = gdi.CreateCompatibleDC(screen)
        bitmap = gdi.CreateCompatibleBitmap(screen, width, height)
        if not memory or not bitmap:
            raise OSError("Cannot allocate the screenshot bitmap.")
        previous = gdi.SelectObject(memory, bitmap)
        if not previous or previous == ctypes.c_void_p(-1).value:
            raise OSError("Cannot select the screenshot bitmap.")
        # PW_CLIENTONLY | PW_RENDERFULLCONTENT captures the native child HWND,
        # not QWidget.grab(), which omits the separately rendered game surface.
        if not user32.PrintWindow(hwnd, memory, 3):
            raise OSError("Windows could not capture the game surface.")
        gdi.SelectObject(memory, previous)
        previous = None
        header = bytearray(44)
        struct.pack_into("<IiiHHIIiiII", header, 0, 40, width, -height, 1, 32, 0, width * height * 4, 0, 0, 0, 0)
        info = (ctypes.c_char * 44).from_buffer(header)
        pixels = ctypes.create_string_buffer(width * height * 4)
        if gdi.GetDIBits(memory, bitmap, 0, height, pixels, info, 0) != height:
            raise OSError("Cannot read the captured game pixels.")
        image = QImage(pixels.raw, width, height, width * 4, QImage.Format_RGB32).copy()
        if image.isNull():
            raise OSError("Windows returned an empty screenshot.")
        if all(high == 0 for low, high in Image.frombytes("RGB", (width, height),
                                                         pixels.raw, "raw", "BGRX").getextrema()):
            raise ValueError("Windows returned a black game surface. Make the game visible and try again.")
        return image
    finally:
        if previous and memory:
            gdi.SelectObject(memory, previous)
        if bitmap:
            gdi.DeleteObject(bitmap)
        if memory:
            gdi.DeleteDC(memory)
        if screen:
            user32.ReleaseDC(hwnd, screen)


def capture_game(runtime) -> tuple[Path, int, int]:
    if not runtime.running():
        raise ValueError("Start the wrapper first.")
    image = capture_client(runtime.game_window())
    directory = Path(config.APPDATA) / "screenshots"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / ("FH2-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f") + ".png")
    if not image.save(str(path), "PNG"):
        raise OSError("Cannot save screenshot to " + str(path))
    return path, image.width(), image.height()
