"""Mounted loose-file and ZIP browsing with shared read-only binary inspection."""
import os
import sqlite3
from contextlib import closing
from pathlib import Path
import zipfile

from PySide6.QtCore import Qt, Signal, QDir, QSortFilterProxyModel, QTimer
from PySide6.QtGui import QStandardItemModel, QStandardItem
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QLineEdit,
                               QSpinBox, QTabWidget, QSplitter, QTreeView, QFileSystemModel,
                               QFileDialog, QMessageBox, QTableWidget, QTableWidgetItem, QComboBox,
                               QHeaderView)

from .assets import SUPPORTED_EXT, inspect_binary, strings, hex_page
from .editors import CodeEditor


class SQLiteViewer(QWidget):
    PAGE_SIZE = 100
    TEXT_PREVIEW_LENGTH = 2048
    BLOB_PREVIEW_LENGTH = 64

    def __init__(self, path, temporary_path=False):
        super().__init__()
        self.path = os.path.abspath(path)
        self.temporary_path = temporary_path
        if temporary_path:
            temporary_file = self.path
            self.destroyed.connect(lambda _obj=None: SQLiteViewer._remove_temporary_file(temporary_file))
        layout = QVBoxLayout(self)
        controls = QHBoxLayout()
        controls.addWidget(QLabel("Table:"))
        self.table = QComboBox()
        self.table.currentTextChanged.connect(self._table_changed)
        controls.addWidget(self.table)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search values in this table...")
        controls.addWidget(self.search, 1)
        self.previous = QPushButton("Previous")
        self.next = QPushButton("Next")
        controls.addWidget(self.previous)
        controls.addWidget(self.next)
        layout.addLayout(controls)
        self.schema = QLabel()
        self.schema.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.schema)
        self.status = QLabel()
        layout.addWidget(self.status)
        self.rows = QTableWidget()
        self.rows.setEditTriggers(QTableWidget.NoEditTriggers)
        self.rows.setAlternatingRowColors(True)
        self.rows.setWordWrap(False)
        self.rows.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.rows.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.rows, 1)
        self.page = 0
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.setInterval(250)
        self.search.textChanged.connect(self._schedule_refresh)
        self.previous.clicked.connect(self._previous_page)
        self.next.clicked.connect(self._next_page)
        self._load_tables()

    def _connect(self):
        uri = Path(self.path).resolve().as_uri() + "?mode=ro"
        connection = sqlite3.connect(uri, uri=True, timeout=5)
        connection.execute("PRAGMA query_only=ON")
        return connection

    def _load_tables(self):
        try:
            with closing(self._connect()) as connection:
                names = [row[0] for row in connection.execute(
                    "SELECT name FROM sqlite_schema WHERE type='table' AND name NOT LIKE 'sqlite_%' "
                    "ORDER BY name COLLATE NOCASE")]
        except (OSError, sqlite3.Error) as ex:
            self.status.setText("Cannot open SQLite database read-only: %s" % ex)
            self.table.setEnabled(False)
            self.search.setEnabled(False)
            self.previous.setEnabled(False)
            self.next.setEnabled(False)
            return
        self.table.addItems(names)
        if not names:
            self.status.setText("No user tables found in this database.")

    @staticmethod
    def _quoted(identifier):
        return '"' + identifier.replace('"', '""') + '"'

    def _table_changed(self, _name):
        self.page = 0
        self.refresh()

    def _schedule_refresh(self):
        self.page = 0
        self._refresh_timer.start()

    def _previous_page(self):
        if self.page:
            self.page -= 1
            self.refresh()

    def _next_page(self):
        self.page += 1
        if not self.refresh():
            self.page = max(0, self.page - 1)
            self.refresh()

    def refresh(self):
        table_name = self.table.currentText()
        if not table_name:
            return False
        table = self._quoted(table_name)
        try:
            with closing(self._connect()) as connection:
                columns = [row[1] for row in connection.execute("PRAGMA table_info(%s)" % table)]
                if not columns:
                    raise sqlite3.DatabaseError("Selected table no longer exists.")
                predicates = []
                values = []
                needle = self.search.text().strip()
                if needle:
                    predicates = ["CAST(%s AS TEXT) LIKE ? ESCAPE '\\'" % self._quoted(column)
                                  for column in columns]
                    escaped = needle.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                    values = ["%" + escaped + "%"] * len(predicates)
                where = " WHERE " + " OR ".join(predicates) if predicates else ""
                projected = []
                for column in columns:
                    quoted = self._quoted(column)
                    projected.append(
                        "CASE WHEN typeof({0})='blob' THEN printf('[BLOB %d bytes] %s',length({0}),"
                        "hex(substr({0},1,{1}))) WHEN typeof({0})='text' AND length({0})>{2} THEN "
                        "substr({0},1,{2})||'…' ELSE {0} END AS {3}".format(
                            quoted, self.BLOB_PREVIEW_LENGTH, self.TEXT_PREVIEW_LENGTH, quoted))
                rows = connection.execute(
                    "SELECT %s FROM %s%s LIMIT ? OFFSET ?" %
                    (", ".join(projected), table, where),
                    values + [self.PAGE_SIZE + 1, self.page * self.PAGE_SIZE]).fetchall()
            has_next = len(rows) > self.PAGE_SIZE
            rows = rows[:self.PAGE_SIZE]
            self.rows.clear()
            self.rows.setColumnCount(len(columns))
            self.rows.setHorizontalHeaderLabels(columns)
            self.rows.setRowCount(len(rows))
            for row_index, row in enumerate(rows):
                for column_index, value in enumerate(row):
                    item = QTableWidgetItem("" if value is None else str(value))
                    self.rows.setItem(row_index, column_index, item)
            self.schema.setText("Columns: " + ", ".join(columns))
            start = self.page * self.PAGE_SIZE
            end = start + len(rows)
            self.status.setText("Read-only database | rows %s-%s | page %d" %
                                (start + 1 if rows else 0, end, self.page + 1))
            self.previous.setEnabled(self.page > 0)
            self.next.setEnabled(has_next)
            return True
        except (OSError, sqlite3.Error) as ex:
            self.status.setText("Cannot read table: %s" % ex)
            self.rows.setRowCount(0)
            self.previous.setEnabled(False)
            self.next.setEnabled(False)
            return False

    def closeEvent(self, event):
        if self.temporary_path:
            self._remove_temporary_file(self.path)
        super().closeEvent(event)

    @staticmethod
    def _remove_temporary_file(path):
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
        except OSError:
            pass


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
