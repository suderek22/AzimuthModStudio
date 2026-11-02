"""Swatchbin viewer and 3D model viewer."""
import hashlib
import math

import numpy as np
from PySide6.QtCore import Qt, QPointF, Signal
from PySide6.QtGui import QImage, QPixmap, QPainter, QColor, QBrush, QSurfaceFormat
from PySide6.QtOpenGLWidgets import QOpenGLWidget
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QCheckBox,
                               QFileDialog, QListWidget, QListWidgetItem, QSplitter, QSlider, QMessageBox)

from . import theme


class ImageCanvas(QWidget):
    def __init__(self):
        super().__init__()
        self.img = None
        self.zoom = 1.0
        self.off = QPointF(0, 0)
        self._drag = None
        self.setMinimumSize(200, 200)

    def set_image(self, qimg):
        self.img = qimg
        self.fit()

    def fit(self):
        if self.img:
            self.zoom = min(self.width() / self.img.width(), self.height() / self.img.height()) * 0.95
            self.off = QPointF((self.width() - self.img.width() * self.zoom) / 2,
                               (self.height() - self.img.height() * self.zoom) / 2)
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(theme.EDITOR_BG))
        if not self.img:
            return
        r = (self.off.x(), self.off.y(), self.img.width() * self.zoom, self.img.height() * self.zoom)
        # checkerboard behind transparent pixels
        p.save()
        p.setClipRect(*[int(v) for v in r])
        s = 12
        for y in range(int(r[1]), int(r[1] + r[3]) + s, s):
            for x in range(int(r[0]), int(r[0] + r[2]) + s, s):
                c = "#3A3C42" if ((x - int(r[0])) // s + (y - int(r[1])) // s) % 2 else "#2F3136"
                p.fillRect(x, y, s, s, QColor(c))
        p.restore()
        p.setRenderHint(QPainter.SmoothPixmapTransform, self.zoom < 1)
        p.drawImage(int(r[0]), int(r[1]), self.img.scaled(int(r[2]), int(r[3]), Qt.IgnoreAspectRatio,
                                                         Qt.SmoothTransformation if self.zoom < 1 else Qt.FastTransformation))

    def wheelEvent(self, e):
        f = 1.15 if e.angleDelta().y() > 0 else 1 / 1.15
        pos = e.position()
        self.off = pos - (pos - self.off) * f
        self.zoom *= f
        self.update()

    def mousePressEvent(self, e):
        self._drag = e.position()

    def mouseMoveEvent(self, e):
        if self._drag is not None:
            self.off += e.position() - self._drag
            self._drag = e.position()
            self.update()

    def mouseReleaseEvent(self, e):
        self._drag = None

    def mouseDoubleClickEvent(self, e):
        self.fit()


class SwatchViewer(QWidget):
    def __init__(self, name, data):
        super().__init__()
        self.name = name
        from . import swatch
        self.err = None
        self.tex = None
        try:
            self.tex = swatch.load_texture(data)
            self.rgba = self.tex.rgba()
        except Exception as ex:  # shown to the user in the info label
            self.err = str(ex)
            self.rgba = np.zeros((4, 4, 4), np.uint8)
        self.chk = {}
        top = QHBoxLayout()
        top.setContentsMargins(6, 4, 6, 4)
        for ch in "RGBA":
            c = QCheckBox(ch)
            c.setChecked(True)
            c.toggled.connect(self.refresh)
            self.chk[ch] = c
            top.addWidget(c)
        b = QPushButton("Export PNG…")
        b.clicked.connect(self.export_png)
        top.addWidget(b)
        b2 = QPushButton("Fit")
        b2.clicked.connect(lambda: self.canvas.fit())
        top.addWidget(b2)
        self.info = QLabel()
        self.info.setStyleSheet("color:%s;" % (theme.RED if self.err else theme.FG_DIM))
        top.addWidget(self.info)
        top.addStretch()
        self.canvas = ImageCanvas()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addLayout(top)
        lay.addWidget(self.canvas)
        if self.tex:
            self.info.setText("%dx%d  %s  %d mip(s)" % (self.tex.width, self.tex.height, self.tex.format, self.tex.mips))
        else:
            self.info.setText("Cannot decode: %s" % self.err)
        self.refresh()

    def refresh(self):
        a = self.rgba.copy()
        for i, ch in enumerate("RGBA"):
            if not self.chk[ch].isChecked():
                a[..., i] = 0 if ch != "A" else 255
        if not self.chk["A"].isChecked():
            a[..., 3] = 255
        a = np.ascontiguousarray(a)
        self._buf = a
        img = QImage(a.data, a.shape[1], a.shape[0], a.shape[1] * 4, QImage.Format_RGBA8888)
        self.canvas.set_image(img.copy())

    def export_png(self):
        p, _ = QFileDialog.getSaveFileName(self, "Export PNG", self.name.rsplit("/", 1)[-1].replace(".swatchbin", ".png"),
                                           "PNG (*.png)")
        if p:
            QImage(self._buf.data, self._buf.shape[1], self._buf.shape[0], self._buf.shape[1] * 4,
                   QImage.Format_RGBA8888).save(p)


# --------------------------------------------------------------------------- 3D
def _color_for(name):
    h = hashlib.md5(name.encode()).digest()
    return 0.35 + h[0] / 255 * 0.55, 0.35 + h[1] / 255 * 0.55, 0.35 + h[2] / 255 * 0.55


class GLView(QOpenGLWidget):
    def __init__(self):
        super().__init__()
        self.meshes = []     # dict(name, pos, nrm, tris, color, visible)
        self.yaw, self.pitch, self.dist = 35.0, 15.0, 6.0
        self.center = np.zeros(3)
        self.wire = False
        self.flat = False
        self._last = None
        self._btn = None
        self.setMinimumSize(300, 300)

    def set_meshes(self, meshes):
        self.meshes = meshes
        pts = [m["pos"] for m in meshes if m["visible"] and len(m["pos"])]
        if pts:
            allp = np.concatenate(pts)
            lo, hi = allp.min(0), allp.max(0)
            self.center = (lo + hi) / 2
            self.dist = float(np.linalg.norm(hi - lo)) * 1.2 + 0.1
        self.update()

    def initializeGL(self):
        from OpenGL import GL as gl
        gl.glEnable(gl.GL_DEPTH_TEST)
        gl.glEnable(gl.GL_NORMALIZE)
        gl.glClearColor(0.118, 0.122, 0.133, 1)

    def paintGL(self):
        from OpenGL import GL as gl
        from OpenGL import GLU as glu
        gl.glClear(gl.GL_COLOR_BUFFER_BIT | gl.GL_DEPTH_BUFFER_BIT)
        w, h = max(1, self.width()), max(1, self.height())
        ratio = self.devicePixelRatioF()
        gl.glViewport(0, 0, int(w * ratio), int(h * ratio))
        gl.glMatrixMode(gl.GL_PROJECTION)
        gl.glLoadIdentity()
        glu.gluPerspective(40, w / h, max(self.dist / 500, 0.001), self.dist * 20)
        gl.glMatrixMode(gl.GL_MODELVIEW)
        gl.glLoadIdentity()
        gl.glTranslatef(0, 0, -self.dist)
        gl.glRotatef(self.pitch, 1, 0, 0)
        gl.glRotatef(self.yaw, 0, 1, 0)
        gl.glTranslatef(*(-self.center))
        gl.glEnable(gl.GL_LIGHTING)
        gl.glEnable(gl.GL_LIGHT0)
        gl.glEnable(gl.GL_COLOR_MATERIAL)
        gl.glLightfv(gl.GL_LIGHT0, gl.GL_POSITION, (0.4, 1.0, 0.6, 0.0))
        gl.glLightModeli(gl.GL_LIGHT_MODEL_TWO_SIDE, 1)
        gl.glPolygonMode(gl.GL_FRONT_AND_BACK, gl.GL_LINE if self.wire else gl.GL_FILL)
        gl.glEnableClientState(gl.GL_VERTEX_ARRAY)
        for m in self.meshes:
            if not m["visible"] or not len(m["tris"]):
                continue
            gl.glColor3f(*m["color"])
            gl.glVertexPointer(3, gl.GL_FLOAT, 0, m["pos32"])
            if m["nrm32"] is not None and not self.flat:
                gl.glEnableClientState(gl.GL_NORMAL_ARRAY)
                gl.glNormalPointer(gl.GL_FLOAT, 0, m["nrm32"])
            else:
                gl.glDisableClientState(gl.GL_NORMAL_ARRAY)
                gl.glNormal3f(0, 1, 0)
            gl.glDrawElements(gl.GL_TRIANGLES, len(m["idx32"]), gl.GL_UNSIGNED_INT, m["idx32"])
        gl.glDisableClientState(gl.GL_VERTEX_ARRAY)
        gl.glDisableClientState(gl.GL_NORMAL_ARRAY)
        gl.glDisable(gl.GL_LIGHTING)
        # ground grid
        gl.glPolygonMode(gl.GL_FRONT_AND_BACK, gl.GL_FILL)
        gl.glColor3f(0.25, 0.26, 0.29)
        gl.glBegin(gl.GL_LINES)
        n, s = 10, self.dist / 6
        for i in range(-n, n + 1):
            gl.glVertex3f(i * s, 0, -n * s)
            gl.glVertex3f(i * s, 0, n * s)
            gl.glVertex3f(-n * s, 0, i * s)
            gl.glVertex3f(n * s, 0, i * s)
        gl.glEnd()

    def mousePressEvent(self, e):
        self._last = e.position()
        self._btn = e.button()

    def mouseMoveEvent(self, e):
        if self._last is None:
            return
        d = e.position() - self._last
        self._last = e.position()
        if self._btn == Qt.LeftButton:
            self.yaw += d.x() * 0.5
            self.pitch = max(-89, min(89, self.pitch + d.y() * 0.5))
        else:
            k = self.dist * 0.0015
            yaw = math.radians(self.yaw)
            right = np.array([math.cos(yaw), 0, math.sin(yaw)])
            self.center = self.center - right * d.x() * k + np.array([0, 1, 0]) * d.y() * k
        self.update()

    def wheelEvent(self, e):
        self.dist *= 0.9 if e.angleDelta().y() > 0 else 1.1
        self.update()


class ModelViewer(QWidget):
    def __init__(self, name, data):
        super().__init__()
        from .model import load_model
        self.name = name
        self.gl = GLView()
        try:
            md = load_model(data)
            err = None
        except Exception as ex:
            md, err = None, str(ex)
        self.list = QListWidget()
        self.meshes = []
        notes = []
        if md:
            for i, m in enumerate(md.meshes):
                pos = np.ascontiguousarray(m.pos, dtype=np.float32)
                nrm = None
                if m.nrm is not None and len(m.nrm) == len(pos):
                    nrm = np.ascontiguousarray(m.nrm, dtype=np.float32)
                mat = md.materials[m.material] if m.material < len(md.materials) else "mat%d" % m.material
                mesh = {"name": m.name, "pos": pos, "pos32": pos, "nrm32": nrm,
                        "tris": m.tris, "idx32": np.ascontiguousarray(m.tris.reshape(-1), dtype=np.uint32),
                        "color": _color_for(mat), "visible": True, "mat": mat}
                self.meshes.append(mesh)
                it = QListWidgetItem("%s  [%s]" % (m.name, mat))
                it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
                it.setCheckState(Qt.Checked)
                self.list.addItem(it)
            notes = md.notes
        self.list.itemChanged.connect(self._toggled)
        self.gl.set_meshes(self.meshes)
        wire = QCheckBox("Wireframe")
        wire.toggled.connect(lambda v: (setattr(self.gl, "wire", v), self.gl.update()))
        flat = QCheckBox("Unlit normals")
        flat.toggled.connect(lambda v: (setattr(self.gl, "flat", v), self.gl.update()))
        allb = QPushButton("Show all")
        allb.clicked.connect(lambda: self._set_all(True))
        noneb = QPushButton("Hide all")
        noneb.clicked.connect(lambda: self._set_all(False))
        tris = sum(len(m["tris"]) for m in self.meshes)
        info = QLabel(("%d meshes, %d triangles" % (len(self.meshes), tris)) if md else "Cannot load model: %s" % err)
        info.setStyleSheet("color:%s;" % (theme.FG_DIM if md else theme.RED))
        if notes:
            info.setToolTip("\n".join(notes[:20]))
        bar = QHBoxLayout()
        bar.setContentsMargins(6, 4, 6, 4)
        for w in (wire, flat, allb, noneb, info):
            bar.addWidget(w)
        bar.addStretch()
        hint = QLabel("LMB orbit · RMB/MMB pan · wheel zoom")
        hint.setStyleSheet("color:%s;" % theme.FG_DIM)
        bar.addWidget(hint)
        sp = QSplitter()
        sp.addWidget(self.gl)
        sp.addWidget(self.list)
        sp.setSizes([900, 260])
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addLayout(bar)
        lay.addWidget(sp)

    def _toggled(self, it):
        self.meshes[self.list.row(it)]["visible"] = it.checkState() == Qt.Checked
        self.gl.update()

    def _set_all(self, v):
        self.list.blockSignals(True)
        for i, m in enumerate(self.meshes):
            m["visible"] = v
            self.list.item(i).setCheckState(Qt.Checked if v else Qt.Unchecked)
        self.list.blockSignals(False)
        self.gl.update()


def install_gl_format():
    f = QSurfaceFormat()
    f.setVersion(2, 1)
    f.setProfile(QSurfaceFormat.CompatibilityProfile)
    f.setDepthBufferSize(24)
    f.setSamples(4)
    QSurfaceFormat.setDefaultFormat(f)
