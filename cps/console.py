"""Session console, history, scroll following and log filters."""
import html
from collections import deque

from PySide6.QtCore import Qt, Signal, QEvent
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QPlainTextEdit,
                               QLineEdit, QDialog, QComboBox, QListWidget, QLabel)

from . import theme


class ConsoleWindow(QDialog):
    attach_requested = Signal()

    def closeEvent(self, event):
        event.ignore()
        self.attach_requested.emit()


class Console(QWidget):
    submit = Signal(str)

    def __init__(self):
        super().__init__()
        self.entries = deque(maxlen=5000)
        self.filters = []
        self.follow = True
        self._writing = False
        self.out = QPlainTextEdit()
        self.out.setReadOnly(True)
        self.out.setMaximumBlockCount(5000)
        self.out.verticalScrollBar().valueChanged.connect(self._scrolled)
        self.latest = QPushButton("Latest")
        self.latest.clicked.connect(self.follow_latest)
        self.latest.hide()
        self.inp = QLineEdit()
        self.inp.setPlaceholderText("Console - type help for commands")
        self.inp.returnPressed.connect(self._enter)
        self.inp.installEventFilter(self)
        self.filter_btn = QPushButton("Filters")
        self.filter_btn.clicked.connect(self.manage_filters)
        self.hist, self.hi = [], 0
        self.draft = ""
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(self.out)
        controls = QHBoxLayout()
        controls.addWidget(self.latest)
        controls.addWidget(self.inp, 1)
        controls.addWidget(self.filter_btn)
        lay.addLayout(controls)

    def _scrolled(self, value):
        if not self._writing:
            self.follow = value >= self.out.verticalScrollBar().maximum()
            self.latest.setVisible(not self.follow)

    def follow_latest(self):
        self.follow = True
        self.out.verticalScrollBar().setValue(self.out.verticalScrollBar().maximum())
        self.latest.hide()

    def _matches(self, text, resource):
        for kind in ("text", "resource"):
            value = (text if kind == "text" else resource).casefold()
            included = [term.casefold() for field, term, exclude in self.filters if field == kind and not exclude]
            excluded = [term.casefold() for field, term, exclude in self.filters if field == kind and exclude]
            if included and not any(term in value for term in included):
                return False
            if any(term in value for term in excluded):
                return False
        return True

    def _append(self, text, color):
        bar = self.out.verticalScrollBar()
        position = bar.value()
        self._writing = True
        try:
            self.out.appendHtml('<span style="color:%s;white-space:pre">%s</span>' %
                                (color or theme.FG, html.escape(text)))
            bar.setValue(bar.maximum() if self.follow else position)
        finally:
            self._writing = False
        self.latest.setVisible(not self.follow)

    def write(self, text, color=None, resource="CPS"):
        self.entries.append((text, color, resource, False))
        if self._matches(text, resource):
            self._append(text, color)

    def notice(self, text, color=None):
        """Command feedback stays visible even when the current log filter excludes it."""
        self.entries.append((text, color, "CPS", True))
        self._append(text, color)

    def set_filters(self, filters):
        self.filters = list(filters)
        self.filter_btn.setStyleSheet("background:%s; color:#111;" % theme.GREEN if self.filters else "")
        self.filter_btn.setToolTip("%d active filters" % len(self.filters))
        self._writing = True
        self.out.clear()
        self._writing = False
        for text, color, resource, feedback in self.entries:
            if feedback or self._matches(text, resource):
                self._append(text, color)

    def manage_filters(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("Console filters")
        dialog.resize(520, 320)
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel("Includes: OR within a field, AND between fields. Excludes always hide a match."))
        listing = QListWidget()
        layout.addWidget(listing)
        controls = QHBoxLayout()
        kind = QComboBox()
        kind.addItems(["text", "resource"])
        mode = QComboBox()
        mode.addItems(["include", "exclude"])
        value = QLineEdit()
        value.setPlaceholderText("Case-insensitive match")
        add = QPushButton("Add")
        for widget in (kind, mode, value, add):
            controls.addWidget(widget)
        layout.addLayout(controls)
        buttons = QHBoxLayout()
        remove = QPushButton("Remove selected")
        clear = QPushButton("Clear all")
        done = QPushButton("Close")
        for widget in (remove, clear, done):
            buttons.addWidget(widget)
        layout.addLayout(buttons)

        def refresh():
            listing.clear()
            listing.addItems(["%s %s: %s" % ("exclude" if exclude else "include", field, term)
                              for field, term, exclude in self.filters])

        def add_filter():
            if value.text().strip():
                rule = (kind.currentText(), value.text().strip(), mode.currentText() == "exclude")
                if rule not in self.filters:
                    self.set_filters(self.filters + [rule])
                value.clear()
                refresh()

        def remove_filter():
            row = listing.currentRow()
            if row >= 0:
                self.set_filters(self.filters[:row] + self.filters[row + 1:])
                refresh()

        add.clicked.connect(add_filter)
        remove.clicked.connect(remove_filter)
        clear.clicked.connect(lambda: (self.set_filters([]), refresh()))
        done.clicked.connect(dialog.accept)
        refresh()
        dialog.exec()

    def _enter(self):
        text = self.inp.text().strip()
        if text:
            self.hist.append(text)
            self.hi = len(self.hist)
            self.draft = ""
            self.notice("> " + text, theme.ACCENT)
            self.submit.emit(text)
        self.inp.clear()

    def eventFilter(self, obj, event):
        if obj is self.inp and event.type() == QEvent.KeyPress and event.key() in (Qt.Key_Up, Qt.Key_Down):
            if self.hist:
                if self.hi == len(self.hist):
                    self.draft = self.inp.text()
                step = -1 if event.key() == Qt.Key_Up else 1
                self.hi = max(0, min(len(self.hist), self.hi + step))
                self.inp.setText(self.hist[self.hi] if self.hi < len(self.hist) else self.draft)
            return True
        return super().eventFilter(obj, event)
