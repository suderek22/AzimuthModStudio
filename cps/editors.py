"""Text editor widgets: XML / INI / TXT / LOG with line numbers, highlighting and validation."""
import re
import xml.dom.minidom
from xml.parsers import expat

from PySide6.QtCore import Qt, QRect, QSize, Signal
from PySide6.QtGui import (QColor, QFont, QPainter, QSyntaxHighlighter, QTextCharFormat, QTextFormat,
                           QKeySequence, QShortcut, QTextCursor)
from PySide6.QtWidgets import (QPlainTextEdit, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
                               QPushButton, QTextEdit)

from . import theme


def fmt(color, bold=False, italic=False):
    f = QTextCharFormat()
    f.setForeground(QColor(color))
    if bold:
        f.setFontWeight(QFont.Bold)
    f.setFontItalic(italic)
    return f


class XmlHighlighter(QSyntaxHighlighter):
    def __init__(self, doc):
        super().__init__(doc)
        self.f_tag = fmt("#E8BF6A")
        self.f_attr = fmt("#BABABA")
        self.f_val = fmt("#6A8759")
        self.f_com = fmt("#808080", italic=True)

    def highlightBlock(self, text):
        self.setCurrentBlockState(0)
        start = 0
        if self.previousBlockState() == 1:
            e = text.find("-->")
            if e < 0:
                self.setFormat(0, len(text), self.f_com)
                self.setCurrentBlockState(1)
                return
            self.setFormat(0, e + 3, self.f_com)
            start = e + 3
        for m in re.finditer(r"<!--.*?-->|<!--.*$|</?[\w:.\-]+|/?>|[\w:.\-]+(?==)|\"[^\"]*\"|'[^']*'", text[start:]):
            s = m.group()
            p = m.start() + start
            if s.startswith("<!--"):
                self.setFormat(p, len(s), self.f_com)
                if not s.endswith("-->"):
                    self.setCurrentBlockState(1)
            elif s.startswith("<") or s in ("/>", ">"):
                self.setFormat(p, len(s), self.f_tag)
            elif s[0] in "\"'":
                self.setFormat(p, len(s), self.f_val)
            else:
                self.setFormat(p, len(s), self.f_attr)


class IniHighlighter(QSyntaxHighlighter):
    def __init__(self, doc):
        super().__init__(doc)
        self.f_sec = fmt("#E8BF6A", True)
        self.f_key = fmt("#9876AA")
        self.f_com = fmt("#808080", italic=True)
        self.f_val = fmt("#6A8759")

    def highlightBlock(self, text):
        t = text.lstrip()
        if t.startswith((";", "#")):
            self.setFormat(0, len(text), self.f_com)
        elif t.startswith("["):
            self.setFormat(0, len(text), self.f_sec)
        elif "=" in text:
            i = text.index("=")
            self.setFormat(0, i, self.f_key)
            self.setFormat(i + 1, len(text) - i - 1, self.f_val)


class LogHighlighter(QSyntaxHighlighter):
    RULES = [(re.compile(r"(?i)\b(error|critical|fatal|exception|crash\w*|failed)\b"), "#F75464"),
             (re.compile(r"(?i)\b(warn\w*)\b"), "#E0A030"),
             (re.compile(r"^\[?\d{4}-\d\d-\d\d[ T]\d\d:\d\d:\d\d[^\]\s]*\]?"), "#6897BB"),
             (re.compile(r"0x[0-9A-Fa-f]+"), "#9876AA")]

    def __init__(self, doc):
        super().__init__(doc)
        self.fm = [(r, fmt(c)) for r, c in self.RULES]

    def highlightBlock(self, text):
        for r, f in self.fm:
            for m in r.finditer(text):
                self.setFormat(m.start(), m.end() - m.start(), f)


class _Gutter(QWidget):
    def __init__(self, ed):
        super().__init__(ed)
        self.ed = ed

    def sizeHint(self):
        return QSize(self.ed.gutter_width(), 0)

    def paintEvent(self, e):
        self.ed.paint_gutter(e)


class CodeEditor(QPlainTextEdit):
    def __init__(self):
        super().__init__()
        f = QFont("JetBrains Mono")
        if not f.exactMatch():
            f = QFont("Consolas")
        f.setPointSize(10)
        self.setFont(f)
        self.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.setTabStopDistance(self.fontMetrics().horizontalAdvance(" ") * 4)
        self.gutter = _Gutter(self)
        self.blockCountChanged.connect(self._update_width)
        self.updateRequest.connect(self._update_gutter)
        self.cursorPositionChanged.connect(self._hl_line)
        self._update_width()
        self._hl_line()

    def gutter_width(self):
        return 14 + self.fontMetrics().horizontalAdvance("9") * len(str(max(1, self.blockCount())))

    def _update_width(self):
        self.setViewportMargins(self.gutter_width(), 0, 0, 0)

    def _update_gutter(self, rect, dy):
        if dy:
            self.gutter.scroll(0, dy)
        else:
            self.gutter.update(0, rect.y(), self.gutter.width(), rect.height())
        if rect.contains(self.viewport().rect()):
            self._update_width()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        cr = self.contentsRect()
        self.gutter.setGeometry(QRect(cr.left(), cr.top(), self.gutter_width(), cr.height()))

    def paint_gutter(self, ev):
        p = QPainter(self.gutter)
        p.fillRect(ev.rect(), QColor(theme.EDITOR_BG))
        p.setPen(QColor(theme.FG_DIM))
        b = self.firstVisibleBlock()
        n = b.blockNumber()
        top = round(self.blockBoundingGeometry(b).translated(self.contentOffset()).top())
        bot = top + round(self.blockBoundingRect(b).height())
        while b.isValid() and top <= ev.rect().bottom():
            if b.isVisible() and bot >= ev.rect().top():
                p.drawText(0, top, self.gutter.width() - 6, self.fontMetrics().height(),
                           Qt.AlignRight, str(n + 1))
            b = b.next()
            top = bot
            bot = top + round(self.blockBoundingRect(b).height())
            n += 1

    def _hl_line(self):
        sel = QTextEdit.ExtraSelection()
        sel.format.setBackground(QColor("#26282E"))
        sel.format.setProperty(QTextFormat.FullWidthSelection, True)
        sel.cursor = self.textCursor()
        sel.cursor.clearSelection()
        self.setExtraSelections([sel])


class TextEditorTab(QWidget):
    """Editor for one file with validation (XML) and save."""
    modified = Signal(bool)
    saved = Signal(str)

    def __init__(self, name, text, kind=None, save_cb=None, hint=""):
        super().__init__()
        self.name = name
        self.save_cb = save_cb
        ext = name.lower().rsplit(".", 1)[-1] if "." in name else ""
        self.kind = kind or {"xml": "xml", "xaml": "xml", "ini": "ini", "log": "log", "txt": "txt"}.get(ext, "txt")
        self.editor = CodeEditor()
        self.editor.setPlainText(text)
        hl = {"xml": XmlHighlighter, "ini": IniHighlighter, "log": LogHighlighter}.get(self.kind)
        self.hl = hl(self.editor.document()) if hl else None
        self.status = QLabel("")
        self.status.setStyleSheet("padding: 2px 8px;")
        bar = QHBoxLayout()
        bar.setContentsMargins(6, 4, 6, 4)
        self.find = QLineEdit()
        self.find.setPlaceholderText("Find (Enter = next)")
        self.find.setMaximumWidth(220)
        self.find.returnPressed.connect(self.find_next)
        bar.addWidget(self.find)
        if self.kind == "xml":
            b = QPushButton("Format")
            b.clicked.connect(self.format_xml)
            bar.addWidget(b)
        self.save_btn = QPushButton("Save")
        self.save_btn.setObjectName("primary")
        self.save_btn.clicked.connect(self.save)
        bar.addWidget(self.save_btn)
        if hint:
            h = QLabel(hint)
            h.setStyleSheet("color:%s;" % theme.FG_DIM)
            bar.addWidget(h)
        bar.addStretch()
        bar.addWidget(self.status)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addLayout(bar)
        lay.addWidget(self.editor)
        self.editor.document().modificationChanged.connect(self.modified)
        self.editor.textChanged.connect(self.validate)
        QShortcut(QKeySequence("Ctrl+S"), self, self.save)
        QShortcut(QKeySequence("Ctrl+F"), self, lambda: self.find.setFocus())
        self.validate()

    def text(self):
        return self.editor.toPlainText()

    def find_next(self):
        if not self.editor.find(self.find.text()):
            self.editor.moveCursor(QTextCursor.Start)
            self.editor.find(self.find.text())

    def validate(self):
        if self.kind != "xml":
            self.status.setText("")
            return
        try:
            expat.ParserCreate().Parse(self.text().encode("utf-8"), True)
            self.status.setText("XML OK")
            self.status.setStyleSheet("padding: 2px 8px; color:%s;" % theme.GREEN)
        except expat.ExpatError as e:
            self.status.setText("XML error: %s" % e)
            self.status.setStyleSheet("padding: 2px 8px; color:%s;" % theme.RED)

    def format_xml(self):
        try:
            d = xml.dom.minidom.parseString(self.text().encode("utf-8"))
            self.editor.setPlainText("\n".join(l for l in d.toprettyxml(indent="  ").splitlines() if l.strip()))
        except Exception as e:
            self.status.setText("Format failed: %s" % e)

    def save(self):
        if self.save_cb:
            self.save_cb(self.name, self.text())
        self.editor.document().setModified(False)
        self.saved.emit(self.name)
