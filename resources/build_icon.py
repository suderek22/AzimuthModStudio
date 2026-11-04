"""Rebuild original application artwork; requires the app's PySide6 and Pillow."""
import io
from pathlib import Path

from PIL import Image
from PySide6.QtCore import QBuffer, QIODevice, QRectF
from PySide6.QtGui import QImage, QPainter
from PySide6.QtSvg import QSvgRenderer


def build():
    root = Path(__file__).resolve().parent
    renderer = QSvgRenderer(str(root / "app-icon.svg"))
    if not renderer.isValid():
        raise ValueError("Invalid original icon SVG")
    image = QImage(1024, 1024, QImage.Format_ARGB32_Premultiplied)
    image.fill(0)
    painter = QPainter(image)
    renderer.render(painter, QRectF(0, 0, 1024, 1024))
    painter.end()
    buffer = QBuffer()
    buffer.open(QIODevice.WriteOnly)
    if not image.save(buffer, "PNG"):
        raise OSError("Cannot render original icon")
    with Image.open(io.BytesIO(bytes(buffer.data()))) as artwork:
        artwork.save(root / "app.ico", format="ICO", sizes=[(size, size) for size in (16, 24, 32, 48, 64, 128, 256)])
        artwork.resize((256, 256), Image.Resampling.LANCZOS).save(root / "app-icon.png")
        artwork.resize((256, 256), Image.Resampling.LANCZOS).save(root / "app.png")
    print("Rebuilt original SVG artwork as PNG and multi-resolution ICO.")


if __name__ == "__main__":
    build()
