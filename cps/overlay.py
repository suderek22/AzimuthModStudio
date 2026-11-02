"""Telemetry overlay drawn over the embedded game (frameless, translucent, click-through window)."""
from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import QColor, QPainter, QFont
from PySide6.QtWidgets import QWidget

from . import theme


class TelemetryOverlay(QWidget):
    """Follows a target widget (the game container). Top-left, so it does not collide with ShowFPS (top-right)."""
    W, H = 340, 150

    def __init__(self, target):
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowTransparentForInput |
                         Qt.WindowDoesNotAcceptFocus)
        self.target = target
        self.t = None
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.resize(self.W, self.H)

    def update_data(self, t):
        self.t = t
        if self.isVisible():
            self.update()

    def follow(self, active):
        """Show over the game when active, keep glued to the container's top-left corner."""
        t = self.target
        show = active and t.isVisible() and bool(self.t and self.t.get("valid")) and t.window().isActiveWindow() \
            and not t.window().isMinimized()
        if not show:
            if self.isVisible():
                self.hide()
            return
        p = t.mapToGlobal(t.rect().topLeft())
        self.move(p.x() + 12, p.y() + 12)
        if not self.isVisible():
            self.create()
            top = t.window().windowHandle()
            if top and self.windowHandle():
                self.windowHandle().setTransientParent(top)
            self.show()

    def _bar(self, p, x, y, w, h, v, color):
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(0, 0, 0, 140))
        p.drawRoundedRect(QRectF(x, y, w, h), 3, 3)
        p.setBrush(QColor(color))
        p.drawRoundedRect(QRectF(x, y, w * max(0.0, min(1.0, v)), h), 3, 3)

    def paintEvent(self, _e):
        t = self.t
        if not t or not t.get("valid"):
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setBrush(QColor(20, 21, 23, 150))
        p.setPen(Qt.NoPen)
        p.drawRoundedRect(QRectF(0, 0, self.W, self.H), 8, 8)
        speed = float(t.get("speed", 0)) * 3.6
        rpm, red = float(t.get("rpm", 0)), max(1.0, float(t.get("redline", 1) or 1))
        p.setPen(QColor("#FFFFFF"))
        p.setFont(QFont("Segoe UI", 26, QFont.Bold))
        p.drawText(QRectF(12, 4, 120, 44), Qt.AlignLeft | Qt.AlignVCenter, "%d" % speed)
        p.setFont(QFont("Segoe UI", 8))
        p.setPen(QColor("#B0B3B8"))
        p.drawText(QRectF(12, 46, 120, 14), Qt.AlignLeft, "km/h (%.0f mph)" % (speed / 1.609))
        g = int(t.get("gear", 0))
        p.setFont(QFont("Segoe UI", 30, QFont.Bold))
        p.setPen(QColor(theme.ACCENT))
        p.drawText(QRectF(130, 0, 50, 56), Qt.AlignCenter, "R" if g < 0 else "N" if g == 0 else str(g))
        frac = rpm / red
        self._bar(p, 190, 12, 140, 12, frac * 0.85,
                  theme.RED if frac > 0.95 else theme.ORANGE if frac > 0.8 else theme.GREEN)
        p.setFont(QFont("Consolas", 8))
        p.setPen(QColor("#E0E0E0"))
        p.drawText(QRectF(190, 26, 140, 14), Qt.AlignLeft, "%d / %d rpm" % (rpm, red))
        self._bar(p, 12, 70, 100, 8, float(t.get("throttle", 0)), theme.GREEN)
        self._bar(p, 120, 70, 100, 8, float(t.get("brake", 0)), theme.RED)
        steer = float(t.get("steer", 0))
        self._bar(p, 228, 70, 100, 8, (steer + 1) / 2 if abs(steer) <= 1 else 0.5, theme.ACCENT)
        p.setPen(QColor("#B0B3B8"))
        p.drawText(QRectF(12, 80, 100, 12), Qt.AlignLeft, "Throttle")
        p.drawText(QRectF(120, 80, 100, 12), Qt.AlignLeft, "Brake")
        p.drawText(QRectF(228, 80, 100, 12), Qt.AlignLeft, "Steer")
        p.setPen(QColor("#E0E0E0"))
        rows = ("%s" % t.get("car", "?"),
                "%.0f kW   %.0f Nm   boost %.2f" % (float(t.get("power", 0)) / 1000, float(t.get("torque", 0)),
                                                    float(t.get("boost", 0))),
                "pos %.0f %.0f %.0f   ABS/TCS/STM %s/%s/%s" % (t.get("x", 0), t.get("y", 0), t.get("z", 0),
                                                              t.get("abs", "-"), t.get("tcs", "-"), t.get("stm", "-")))
        for i, r in enumerate(rows):
            p.drawText(QRectF(12, 96 + i * 17, self.W - 20, 16), Qt.AlignLeft, r)
