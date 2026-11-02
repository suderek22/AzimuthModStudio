"""Mounted loose-file and ZIP browsing with shared read-only binary inspection."""
import os
import zipfile

from PySide6.QtCore import Qt, Signal, QDir, QSortFilterProxyModel
from PySide6.QtGui import QStandardItemModel, QStandardItem
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QLineEdit,
                               QSpinBox, QTabWidget, QSplitter, QTreeView, QFileSystemModel,
                               QFileDialog, QMessageBox)

from .assets import SUPPORTED_EXT, inspect_binary, strings, hex_page
from .editors import CodeEditor


class BinaryViewer(QWidget):
    def __init__(self, name, data):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        tabs = QTabWidget()
        layout.addWidget(tabs)
        structure = CodeEditor()
        structure.setReadOnly(True)
        try:
            structure.setPlainText(inspect_binary(name, data))
        except (ValueError, IndexError, UnicodeError) as ex:
            structure.setPlainText("Cannot decode structure: %s\nRaw bytes and strings remain available." % ex)
        tabs.addTab(structure, "Structure (read-only)")
        string_view = CodeEditor()
        string_view.setReadOnly(True)
        rows, limited = strings(data)
        string_view.setPlainText("\n".join("%08X  %s" % row for row in rows) +
                                 ("\nString listing limited to 5,000 results." if limited else ""))
        tabs.addTab(string_view, "Strings / references")
        raw = QWidget()
        raw_layout = QVBoxLayout(raw)
        controls = QHBoxLayout()
        controls.addWidget(QLabel("%s - %s bytes. Page (64 KiB):" % (name, format(len(data), ","))))
        self.page = QSpinBox()
        self.page.setRange(1, max(1, (len(data) + 65535) // 65536))
        controls.addWidget(self.page)
        controls.addWidget(QLabel("/ %d" % self.page.maximum()))
        controls.addStretch()
        raw_layout.addLayout(controls)
        self.hex = CodeEditor()
        self.hex.setReadOnly(True)
        raw_layout.addWidget(self.hex)
        self.page.valueChanged.connect(lambda value: self.hex.setPlainText(hex_page(data, (value - 1) * 65536)))
        self.hex.setPlainText(hex_page(data))
        tabs.addTab(raw, "Bytes")


class GameFilesManager(QWidget):
    open_requested = Signal(str, object)

    def __init__(self, root):
        super().__init__()
        self.archive_path = None
        layout = QVBoxLayout(self)
        top = QHBoxLayout()
        self.location = QLineEdit(os.path.abspath(root) if root else "")
        self.location.setReadOnly(True)
        browse = QPushButton("Choose folder...")
        browse.clicked.connect(self.choose_root)
        top.addWidget(self.location, 1)
        top.addWidget(browse)
        layout.addLayout(top)
        layout.addWidget(QLabel("Double-click supported loose files or ZIP entries to open them in a tab. "
                               "Archives and binary assets are read-only; text files can be saved."))
        split = QSplitter()
        layout.addWidget(split)
        self.fs = QFileSystemModel(self)
        self.fs.setFilter(QDir.AllDirs | QDir.Files | QDir.NoDotAndDotDot)
        self.fs.setNameFilters(["*" + ext for ext in SUPPORTED_EXT] + ["*.zip"])
        self.fs.setNameFilterDisables(False)
        self.tree = QTreeView()
        self.tree.setModel(self.fs)
        self.tree.setColumnWidth(0, 360)
        self.tree.doubleClicked.connect(self._activate)
        self.tree.clicked.connect(self._selected)
        split.addWidget(self.tree)
        archive = QWidget()
        archive_layout = QVBoxLayout(archive)
        self.archive_title = QLabel("Select a ZIP archive to browse its contents.")
        self.archive_title.setWordWrap(True)
        archive_layout.addWidget(self.archive_title)
        search = QLineEdit()
        search.setPlaceholderText("Filter archive entries...")
        archive_layout.addWidget(search)
        self.entries = QStandardItemModel(0, 2)
        self.entries.setHorizontalHeaderLabels(["Archive entry", "Bytes"])
        self.proxy = QSortFilterProxyModel(self)
        self.proxy.setSourceModel(self.entries)
        self.proxy.setFilterCaseSensitivity(Qt.CaseInsensitive)
        self.proxy.setFilterKeyColumn(0)
        search.textChanged.connect(self.proxy.setFilterFixedString)
        self.archive_tree = QTreeView()
        self.archive_tree.setRootIsDecorated(False)
        self.archive_tree.setModel(self.proxy)
        self.archive_tree.setColumnWidth(0, 420)
        self.archive_tree.doubleClicked.connect(self._open_entry)
        archive_layout.addWidget(self.archive_tree)
        split.addWidget(archive)
        split.setSizes([500, 600])
        self.set_root(root)

    def set_root(self, root):
        if root and os.path.isdir(root):
            root = os.path.abspath(root)
            self.location.setText(root)
            self.tree.setRootIndex(self.fs.setRootPath(root))

    def choose_root(self):
        path = QFileDialog.getExistingDirectory(self, "Game files folder", self.location.text())
        if path:
            self.set_root(path)

    def _selected(self, index):
        path = self.fs.filePath(index)
        if path.lower().endswith(".zip") and path != self.archive_path:
            self.load_archive(path)

    def _activate(self, index):
        path = self.fs.filePath(index)
        if os.path.isdir(path):
            return
        if path.lower().endswith(".zip"):
            self.load_archive(path)
        else:
            self.open_requested.emit(path, None)

    def load_archive(self, path):
        self.archive_path = None
        self.entries.removeRows(0, self.entries.rowCount())
        try:
            with zipfile.ZipFile(path) as archive:
                files = [entry for entry in archive.infolist()
                         if not entry.is_dir() and entry.filename.lower().endswith(SUPPORTED_EXT)]
            for entry in sorted(files, key=lambda item: item.filename.casefold()):
                item, size = QStandardItem(entry.filename), QStandardItem(str(entry.file_size))
                item.setEditable(False)
                size.setEditable(False)
                self.entries.appendRow([item, size])
            self.archive_path = path
            self.archive_title.setText("%s - %d supported entries" % (path, len(files)))
        except (OSError, ValueError, zipfile.BadZipFile) as ex:
            self.archive_title.setText("Cannot read archive: %s" % ex)
            QMessageBox.warning(self, "Game files manager", str(ex))

    def _open_entry(self, index):
        if self.archive_path and index.isValid():
            name = self.proxy.index(index.row(), 0).data()
            self.open_requested.emit(self.archive_path, name)
