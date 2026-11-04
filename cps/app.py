"""Car Pack Studio main window."""
import os
import ctypes
import shutil
import sqlite3
import sys
import zipfile

from PySide6.QtCore import Qt, QSortFilterProxyModel, QTimer
from PySide6.QtGui import QAction, QStandardItem, QStandardItemModel, QColor, QFont, QIcon
from PySide6.QtWidgets import (QApplication, QMainWindow, QTabWidget, QDockWidget, QTreeView, QWidget, QVBoxLayout,
                               QHBoxLayout, QLineEdit, QLabel, QPushButton, QFileDialog, QMessageBox, QInputDialog,
                               QMenu, QComboBox, QSpinBox, QFormLayout, QTableWidget, QTableWidgetItem, QPlainTextEdit,
                               QDialog, QDialogButtonBox, QCheckBox, QListWidget, QAbstractItemView, QHeaderView,
                               QTextBrowser, QGroupBox, QStatusBar)

from . import APP_NAME, APP_VERSION, config, theme
from .editors import TextEditorTab
from .diagnose import check_pack, scan_logs
from .gamedata import GameData
from .pack import (Project, PackCar, build_pack, install_pack, uninstall_pack, installed_packs, generate_sql,
                   import_car_archive, car_archive_path, uninstall_car)
from .viewers import SwatchViewer, ModelViewer, install_gl_format
from .wrapper import WrapperTab
from .assets import TEXT_EXT, FILE_FILTER
from .filetools import BinaryViewer, GameFilesManager
from .audioviewer import BankViewer

CREDITS = """
<h2>Credits</h2>
<p>Car Pack Studio bundles and builds on open-source work. Thank you to everyone below.</p>
<ul>
<li><b>Azimuth 1.0.2</b> – the Forza Horizon 2 PC runtime (azimuth.exe) that makes the game run on Windows.</li>
<li><b>FH2 Mod Platform 2.8.0</b> – mod loader, database/string/file override services and command pipe
(Trinculo54 and contributors, MIT licence).</li>
<li><b>FH2 Extra Options 0.3.2</b> – by Arthur (RokkuDayo): skip-intro, media overrides, save-check bypass and more.</li>
<li><b>Virtual Dashboard</b> – the telemetry HTTP API used by the debug overlay.</li>
<li><b>DoorsUnlocked 0.1.5</b> – player-car lookup and animation-state playback, integrated into the CPS DLL.</li>
<li><b>FH2NoPause 1.1.0</b> – keeps the wrapper running when it loses focus; its documented WGI controller
hook and XInput mapping informed the Studio controller-script integration.</li>
<li><b>FM5 import tooling</b> (FM5import / Repack-ForzaZip / SwatchbinConverter) – the zip layout and database
cloning knowledge this tool is based on.</li>
<li><b>ForzaTech Studio</b> – inspiration for the model / swatchbin viewers; its <code>xg.dll</code> detiling library is used
for texture decoding.</li>
<li><b>SatNav Voice Pack Creator</b> – FSB5 bank parsing and XMA wrapping approach used for audio preview/export.</li>
<li><b>FFmpeg</b> – bundled audio decoder, GPLv3-or-later; see bin/ffmpeg-license.txt.
Original build information is available with <code>bin\\ffmpeg.exe -version</code>.</li>
<li><b>Qt for Python (PySide6)</b>, <b>NumPy</b>, <b>PyOpenGL</b>, <b>texture2ddecoder</b>, <b>Pillow</b>.</li>
</ul>
<p>Game content (apart from the application icon below) is NOT included.
You must own Forza Horizon 2 and point the tool to your game's <code>Mount</code> folder.</p>
<p>The application icon is an exported BarnFindWorking texture from the user's game, stored as
<code>resources/app-icon.png</code> and multi-size <code>resources/app.ico</code> for executable packaging.
No other game assets are bundled.</p>
<p><b>Tools → Game files manager</b> browses mounted loose files and ZIP contents.
Supported assets open in the same tabs as car Explorer files; <b>Open file from disk</b> uses the same readers.
Swatchbins offer PNG export of the original decoded texture. Banks list their embedded samples, with
<b>Play</b>, <b>Stop</b> and <b>Export WAV</b>. PCM16, mono/stereo XMA and MPEG are supported;
event-only banks and unsupported codecs report an error rather than writing invalid audio.</p>
<p>Binary inspection is read-only: material/shader bundles show blob metadata; PSO/VSO files show DXBC
chunks; FH2 carbin files show scene headers and references; lights.bin shows attachments and light transforms.
AVPINS opens as XML. Physics definitions and LDFB light presets provide raw header values, strings and
paged bytes, not a semantic collision/preset editor. Unverified fields are explicitly left undecoded.</p>
<p><b>Wrapper → Time &amp; Weather</b> controls the live test environment.
<b>Wrapper → Car → Animations</b> lists the active car's animation clips and requests their states.
Playable states are read directly from the car's active GSF graph.
Playback selects and maintains the full-detail animation branch on the vehicle update thread,
then waits 350 ms before sending the event, following Extra Options' roof-animation preparation.
Clips without a matching state are listed but disabled (gauge and wiper clips are not necessarily
independently playable states).
Requests and faults appear in the wrapper console.</p>
<p>Experimental live reload: once in free roam, enter <code>car 363</code> in the wrapper console.
The native fast-travel loading/arrival sequence reloads the car at the same position.
Use <code>car status</code> to check completion. Radio-left is reserved for reload during these test sessions.
<code>car details</code> reports the source/target, reload stage, native streaming-job state and elapsed time.
The console logs load progress every five seconds and reports a 60-second timeout without forcing
an unfinished load to resume. After a timeout, stop/run the wrapper if loading does not recover.
The <b>Recover previous car</b> button appears after 60 seconds. It restarts in the previous car
at the normal spawn point, not at the exact pre-swap position. Recovery is never automatic.
In the wrapper console, <code>car recover</code> immediately restarts in the previous car, and
<code>car force</code> immediately restarts in the requested target car, even before the timeout.
Neither command skips an unfinished native load. <code>recover 0/1</code> still toggles crash recovery.
Unknown car IDs are rejected against the active game database without starting a reload.
Up/Down in the console recalls commands from this CPS session.
The wrapper's car label follows live telemetry, including swaps through Extra Options.
Test in game sends the selected car ID to a running wrapper without restarting it.
If the wrapper is stopped, it builds/installs the project and launches normally.
New or changed pack assets require stopping and running the wrapper again.</p>
<p><b>Map travel:</b> <code>track TheIsland</code> (549) and <code>track Anthem</code> (542)
use the game's native travel flow without restarting Azimuth. <code>track list</code> lists maps;
<code>track status</code> shows the loading stage and timeout state. Storm Island must be mounted
in Azimuth; native DLC checks remain active. Enter free roam before requesting travel.
<code>tp Anthem &lt;place&gt;</code> is for locations within the loaded mainland, not map changes.
Physical radio-right input is reserved/disabled by the experimental travel flow.</p>
<p><b>Console:</b> scrolling up pauses auto-follow; <b>Latest</b> resumes it.
Repeated <code>[CarPackStudio] command: car status</code> polling lines are hidden from the console.
Use <code>filter text "example"</code> or <code>filter resource "CarPackStudio"</code> to include matching logs;
<code>filter -text "example"</code> and <code>filter -resource "CarPackStudio"</code> exclude matches.
Matching is case-insensitive. Includes are OR within a field, AND between fields; exclusions always win.
<code>filter remove</code> clears all filters. The <b>Filters</b> button manages the same rules and turns green
when any are active; changes also refilter the last 5,000 retained entries. Command feedback stays visible.</p>
<p><code>debug Azimuth</code> or <code>debug "mod name"</code> streams selected sources' available live logs,
plus errors/critical messages from other sources (console filters still apply).
Repeat to add sources; <code>debug off</code> restores normal logging.
Azimuth tracing instruments D3D12 pipeline creation/cache-load API calls, including thread IDs, durations and HRESULTs.
It is not a full native function trace. Mod debugging uses each mod's own log messages.
<code>console 1</code> detaches the console and <code>console 0</code> reattaches it.
Tools → Console detach / Console attach provides the same actions. Closing the separate console reattaches it,
preserving history, filters, input and output. The detached console opens at 1000 × 700
and can be resized, with a 640 × 400 minimum.</p>
<p><b>Pack → Import car ZIP</b> adds an asset archive to the current pack. Select a database/physics donor,
then edit the imported car's name, brand and ID in Car properties. The original ZIP is copied into the project
and used instead of the donor's assets. Renaming requires the same number of characters as the ZIP name.</p>
"""


class SettingsDialog(QDialog):
    def __init__(self, s, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.s = dict(s)
        self.mount = QLineEdit(s["mount"])
        self.launch_args = QLineEdit(s.get("launch_arguments", config.DEFAULTS["launch_arguments"]))
        self.launch_args.setToolTip('Arguments only, without azimuth.exe. Applies on the next launch.\n'
                                   'Use double quotes around paths containing spaces. Leave blank for no arguments.')
        reset_args = QPushButton("Reset to default")
        reset_args.clicked.connect(lambda: self.launch_args.setText(config.DEFAULTS["launch_arguments"]))
        b = QPushButton("Browse…")
        b.clicked.connect(lambda: self._pick(self.mount))
        self.mode = QComboBox()
        self.mode.addItems(["sandbox", "existing"])
        self.mode.setCurrentText(s["runtime_mode"])
        self.az = QLineEdit(s["azimuth_dir"])
        b2 = QPushButton("Browse…")
        b2.clicked.connect(lambda: self._pick(self.az))
        self.sandbox = QLineEdit(s["sandbox_dir"])
        self.port = QSpinBox()
        self.port.setRange(1, 65535)
        self.port.setValue(int(s["telemetry_port"]))
        self.recover = QCheckBox("Keep the game running after a crash (log critical error)")
        self.recover.setChecked(bool(s["crash_recover"]))
        self.boot = QCheckBox("Auto-skip menus (sends Enter until driving)")
        self.boot.setChecked(bool(s["boot_assist"]))
        self.usave = QCheckBox("Sandbox: use the generated ultimate save (all cars, 999,999,999 credits)")
        self.usave.setChecked(bool(s.get("ultimate_save", True)))
        self.festival = QCheckBox("Sandbox: spawn at the festival site (not the car meet)")
        self.festival.setChecked(bool(s.get("spawn_festival", True)))
        self.dbgmap = QLineEdit(s["debug_map"])
        self.dbgmap.setPlaceholderText("(planned) track/entity group to force on start")
        self.dbgmap.setEnabled(False)
        f = QFormLayout()
        r = QHBoxLayout()
        r.addWidget(self.mount)
        r.addWidget(b)
        f.addRow("Game Mount folder", r)
        args_row = QHBoxLayout()
        args_row.addWidget(self.launch_args)
        args_row.addWidget(reset_args)
        f.addRow("Azimuth launch arguments", args_row)
        f.addRow("Runtime", self.mode)
        r2 = QHBoxLayout()
        r2.addWidget(self.az)
        r2.addWidget(b2)
        f.addRow("Existing Azimuth folder", r2)
        f.addRow("Sandbox folder", self.sandbox)
        f.addRow("Telemetry port", self.port)
        f.addRow("", self.recover)
        f.addRow("", self.boot)
        f.addRow("", self.usave)
        f.addRow("", self.festival)
        f.addRow("Debug map", self.dbgmap)
        note = QLabel("Sandbox = a private copy of Azimuth with its own empty saves (recommended).\n"
                      "Existing = use your Azimuth folder; its launcher setting display=windowed is set for embedding.")
        note.setStyleSheet("color:%s;" % theme.FG_DIM)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addLayout(f)
        lay.addWidget(note)
        lay.addWidget(bb)
        self.resize(640, 320)

    def _pick(self, edit):
        d = QFileDialog.getExistingDirectory(self, "Select folder", edit.text())
        if d:
            edit.setText(os.path.normpath(d))

    def result_settings(self):
        s = dict(self.s)
        s.update(mount=self.mount.text().strip(), runtime_mode=self.mode.currentText(),
                 launch_arguments=self.launch_args.text().strip(),
                 azimuth_dir=self.az.text().strip(), sandbox_dir=self.sandbox.text().strip(),
                 telemetry_port=self.port.value(), crash_recover=self.recover.isChecked(),
                 boot_assist=self.boot.isChecked(), ultimate_save=self.usave.isChecked(), spawn_festival=self.festival.isChecked(),
                 debug_map=self.dbgmap.text())
        return s


class CarBrowser(QWidget):
    """Installed + base cars from the game database."""

    def __init__(self, owner):
        super().__init__()
        self.owner = owner
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search brand / name / media name…")
        self.search.textChanged.connect(self._filter)
        self.model = QStandardItemModel(0, 4)
        self.model.setHorizontalHeaderLabels(["ID", "Car", "Media", "Source"])
        self.proxy = QSortFilterProxyModel()
        self.proxy.setSourceModel(self.model)
        self.proxy.setFilterCaseSensitivity(Qt.CaseInsensitive)
        self.proxy.setFilterKeyColumn(-1)
        self.tree = QTreeView()
        self.tree.setModel(self.proxy)
        self.tree.setSortingEnabled(True)
        self.tree.setRootIsDecorated(False)
        self.tree.header().setStretchLastSection(False)
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.tree.header().setSectionResizeMode(1, QHeaderView.Stretch)
        self.tree.doubleClicked.connect(lambda i: self.owner.open_car(self.car_at(i)))
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._menu)
        self.count = QLabel("")
        self.count.setStyleSheet("color:%s; padding:2px 6px;" % theme.FG_DIM)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.addWidget(self.search)
        lay.addWidget(self.tree)
        lay.addWidget(self.count)
        self.cars = []

    def load(self, cars):
        self.cars = cars
        self.model.removeRows(0, self.model.rowCount())
        for c in cars:
            it = QStandardItem(c.title)
            it.setData(c, Qt.UserRole)
            m = QStandardItem(c.media)
            s = QStandardItem("MOD" if c.source == "mod" else "base")
            if c.source == "mod":
                s.setForeground(QColor(theme.GREEN))
            cid = QStandardItem()
            cid.setData(c.id, Qt.DisplayRole)
            cid.setData(c, Qt.UserRole)
            for item in (it, m, s, cid):
                item.setEditable(False)
            self.model.appendRow([cid, it, m, s])
        self.count.setText("%d cars" % len(cars))

    def _filter(self, t):
        self.proxy.setFilterFixedString(t)

    def car_at(self, idx):
        return self.proxy.mapToSource(idx.siblingAtColumn(0)).data(Qt.UserRole)

    def _menu(self, pos):
        idx = self.tree.indexAt(pos)
        if not idx.isValid():
            return
        car = self.car_at(idx)
        m = QMenu(self)
        m.addAction("Open in Explorer", lambda: self.owner.open_car(car))
        m.addAction("Add to pack as new car (clone)…", lambda: self.owner.add_clone(car))
        m.addAction("Test in game", lambda: self.owner.test_base_car(car))
        m.exec(self.tree.viewport().mapToGlobal(pos))


class Explorer(QWidget):
    def __init__(self, owner):
        super().__init__()
        self.owner = owner
        self.title = QLabel("No car open")
        self.title.setStyleSheet("padding:4px 6px; font-weight:600;")
        self.model = QStandardItemModel()
        self.model.setHorizontalHeaderLabels(["Files"])
        self.tree = QTreeView()
        self.tree.setModel(self.model)
        self.tree.setHeaderHidden(True)
        self.tree.doubleClicked.connect(self._open)
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._menu)
        self.filter = QLineEdit()
        self.filter.setPlaceholderText("Filter files…")
        self.filter.textChanged.connect(self.refill)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.addWidget(self.title)
        lay.addWidget(self.filter)
        lay.addWidget(self.tree)
        self.entries = []
        self.car = None

    def set_car(self, car, names, overridden, editable):
        self.car = car
        self.entries = sorted(names)
        self.overridden = overridden
        self.editable = editable
        self.title.setText("%s%s" % (car.title if hasattr(car, "title") else car.name,
                                     "" if editable else "  (base car, read-only)"))
        self.refill()

    def refill(self):
        self.model.removeRows(0, self.model.rowCount())
        flt = self.filter.text().lower()
        dirs = {}
        for n in self.entries:
            if flt and flt not in n.lower():
                continue
            parts = n.split("/")
            parent = self.model.invisibleRootItem()
            path = ""
            for p in parts[:-1]:
                path += p + "/"
                if path not in dirs:
                    d = QStandardItem("📁 " + p)
                    d.setEditable(False)
                    parent.appendRow(d)
                    dirs[path] = d
                parent = dirs[path]
            icon = "🖼 " if n.endswith(".swatchbin") else "🧊 " if n.endswith(".modelbin") else \
                "📝 " if n.lower().endswith(TEXT_EXT) else "📄 "
            it = QStandardItem(icon + parts[-1])
            it.setData(n, Qt.UserRole)
            it.setEditable(False)
            if n in self.overridden:
                it.setForeground(QColor(theme.ORANGE))
                it.setToolTip("Modified in project")
            parent.appendRow(it)
        if flt:
            self.tree.expandAll()
        else:
            self.tree.expandToDepth(0)

    def _name(self, idx):
        return idx.data(Qt.UserRole)

    def _open(self, idx):
        n = self._name(idx)
        if n:
            self.owner.open_file(n)

    def _menu(self, pos):
        idx = self.tree.indexAt(pos)
        n = self._name(idx) if idx.isValid() else None
        if not n:
            return
        m = QMenu(self)
        m.addAction("Open", lambda: self.owner.open_file(n))
        m.addAction("Export…", lambda: self.owner.export_file(n))
        m.addAction("Replace with file…", lambda: self.owner.replace_file(n))
        if n in self.overridden:
            m.addAction("Revert to original", lambda: self.owner.revert_file(n))
        m.exec(self.tree.viewport().mapToGlobal(pos))


class PropertiesPanel(QWidget):
    """Car data, brand and name shown in the game."""

    def __init__(self, owner):
        super().__init__()
        self.owner = owner
        self.car = None
        self.name = QLineEdit()
        self.short = QLineEdit()
        self.year = QSpinBox()
        self.year.setRange(1885, 2100)
        self.make = QComboBox()
        self.make.setEditable(False)
        self.new_make = QCheckBox("Create new brand")
        self.make_name = QLineEdit()
        self.make_name.setPlaceholderText("New brand name")
        self.make_name.setEnabled(False)
        self.new_make.toggled.connect(self.make_name.setEnabled)
        self.car_id = QSpinBox()
        self.car_id.setRange(1000, 999999)
        self.media = QLineEdit()
        self.media.setReadOnly(True)
        self.donor = QLineEdit()
        self.donor.setReadOnly(True)
        self.thumb = QLineEdit()
        self.thumb.setPlaceholderText("(donor's thumbnail)")
        self.ov = QTableWidget(0, 2)
        self.ov.setHorizontalHeaderLabels(["Data_Car column", "Value"])
        self.ov.horizontalHeader().setStretchLastSection(True)
        addb = QPushButton("+ column override")
        addb.clicked.connect(lambda: self.ov.insertRow(self.ov.rowCount()))
        self.apply_btn = QPushButton("Apply")
        self.apply_btn.setObjectName("primary")
        self.apply_btn.clicked.connect(self.apply)
        f = QFormLayout()
        f.addRow("Name (in game)", self.name)
        f.addRow("Short name", self.short)
        f.addRow("Year", self.year)
        f.addRow("Brand", self.make)
        f.addRow("", self.new_make)
        f.addRow("", self.make_name)
        f.addRow("Car ID", self.car_id)
        f.addRow("Media name", self.media)
        f.addRow("Cloned from", self.donor)
        f.addRow("Thumbnail", self.thumb)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.addLayout(f)
        lay.addWidget(QLabel("Extra database values"))
        lay.addWidget(self.ov)
        lay.addWidget(addb)
        lay.addWidget(self.apply_btn)
        self.setEnabled(False)

    def set_makes(self, makes):
        self.make.clear()
        for i, n in sorted(makes.items(), key=lambda kv: kv[1]):
            self.make.addItem(n, i)

    def show_car(self, car):
        self.car = car
        self.setEnabled(car is not None)
        if not car:
            return
        self.name.setText(car.name)
        self.short.setText(car.short)
        self.year.setValue(car.year)
        self.car_id.setValue(car.car_id)
        self.media.setText(car.media)
        self.donor.setText(car.donor)
        self.thumb.setText(car.thumbnail)
        i = self.make.findData(car.make_id)
        self.make.setCurrentIndex(max(0, i))
        self.new_make.setChecked(car.new_make)
        self.make_name.setText(car.make_name)
        self.ov.setRowCount(0)
        for k, v in car.sql_overrides.items():
            r = self.ov.rowCount()
            self.ov.insertRow(r)
            self.ov.setItem(r, 0, QTableWidgetItem(k))
            self.ov.setItem(r, 1, QTableWidgetItem(str(v)))

    def apply(self):
        c = self.car
        if not c:
            return
        c.name, c.short, c.year = self.name.text(), self.short.text(), self.year.value()
        c.car_id = self.car_id.value()
        c.make_id = self.make.currentData() or 0
        c.new_make, c.make_name = self.new_make.isChecked(), self.make_name.text()
        c.thumbnail = self.thumb.text()
        c.sql_overrides = {}
        for r in range(self.ov.rowCount()):
            k, v = self.ov.item(r, 0), self.ov.item(r, 1)
            if k and v and k.text().strip():
                txt = v.text()
                try:
                    c.sql_overrides[k.text().strip()] = int(txt)
                except ValueError:
                    try:
                        c.sql_overrides[k.text().strip()] = float(txt)
                    except ValueError:
                        c.sql_overrides[k.text().strip()] = txt
        self.owner.project_changed()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("%s %s" % (APP_NAME, APP_VERSION))
        self.resize(1600, 960)
        self.s = config.load()
        self.game = None
        self.project = None
        self.current = None        # dict: media, zip_path, packcar|None, names set
        self.zip = None
        self.tabs = QTabWidget()
        self.tabs.setTabsClosable(True)
        self.tabs.setMovable(True)
        self.tabs.tabCloseRequested.connect(self._close_tab)
        self.setCentralWidget(self.tabs)

        self.wrapper = WrapperTab(self.s, self._prepare_for_run)
        self.wrapper.after_cb = self._after_run
        self.wrapper.car_cb = self._selected_car_id
        self.wrapper.car_title_cb = self._car_title
        self.tabs.addTab(self.wrapper, "Wrapper")
        self.tabs.tabBar().setTabButton(0, self.tabs.tabBar().ButtonPosition.RightSide, None)

        self.explorer = Explorer(self)
        self.browser = CarBrowser(self)
        self.props = PropertiesPanel(self)
        self.pack_list = QListWidget()
        self.pack_list.itemDoubleClicked.connect(self._pack_item_open)
        self.pack_list.currentRowChanged.connect(self._pack_selected)
        self.pack_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.pack_list.customContextMenuRequested.connect(self._pack_menu)
        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)

        self._dock("Explorer", self.explorer, Qt.LeftDockWidgetArea)
        d2 = self._dock("Cars", self.browser, Qt.LeftDockWidgetArea)
        self.tabifyDockWidget(self.findChildren(QDockWidget)[0], d2)
        self.findChildren(QDockWidget)[0].raise_()
        pk = QWidget()
        pl = QVBoxLayout(pk)
        pl.setContentsMargins(4, 4, 4, 4)
        pl.addWidget(QLabel("Cars in this pack"))
        pl.addWidget(self.pack_list)
        import_btn = QPushButton("Import car ZIP…")
        import_btn.clicked.connect(self.import_car)
        pl.addWidget(import_btn)
        bb = QHBoxLayout()
        for text, fn in (("Build", self.build), ("Install", self.install), ("Test in game", self.test_in_game)):
            b = QPushButton(text)
            b.clicked.connect(fn)
            if text == "Test in game":
                b.setObjectName("primary")
            bb.addWidget(b)
        pl.addLayout(bb)
        self._dock("Pack", pk, Qt.RightDockWidgetArea)
        self._dock("Car properties", self.props, Qt.RightDockWidgetArea)
        self._dock("Output", self.output, Qt.BottomDockWidgetArea)
        self.setStatusBar(QStatusBar())
        self._menus()
        self.statusBar().showMessage("Ready")
        QTimer.singleShot(100, self._startup)

    def _dock(self, title, w, area):
        d = QDockWidget(title, self)
        d.setWidget(w)
        d.setObjectName(title)
        d.setFeatures(QDockWidget.DockWidgetMovable | QDockWidget.DockWidgetFloatable)
        self.addDockWidget(area, d)
        return d

    def _menus(self):
        mb = self.menuBar()
        f = mb.addMenu("&File")
        for text, fn, sc in (("New pack project…", self.new_project, "Ctrl+N"), ("Open project…", self.open_project, "Ctrl+O"),
                             ("Save project", self.save_project, None), (None, None, None),
                             ("Settings…", self.settings, "Ctrl+,"), (None, None, None), ("Exit", self.close, "Alt+F4")):
            if text is None:
                f.addSeparator()
                continue
            a = QAction(text, self, triggered=fn)
            if sc:
                a.setShortcut(sc)
            f.addAction(a)
        p = mb.addMenu("&Pack")
        p.addAction(QAction("Import car ZIP…", self, triggered=self.import_car))
        p.addAction(QAction("Build pack", self, triggered=self.build))
        p.addAction(QAction("Install into game", self, triggered=self.install))
        p.addAction(QAction("Uninstall a pack…", self, triggered=self.uninstall))
        p.addAction(QAction("Remove selected car from pack", self, triggered=self.remove_car))
        p.addAction(QAction("Show generated SQL", self, triggered=self.show_sql))
        p.addAction(QAction("Export distributable zip…", self, triggered=self.export_zip))
        r = mb.addMenu("&Wrapper")
        r.addAction(QAction("Test selected car in game", self, triggered=self.test_in_game, shortcut="F5"))
        r.addAction(QAction("Stop game", self, triggered=lambda: self.wrapper.stop(), shortcut="Shift+F5"))
        w = r.addMenu("Time && &Weather")
        w.addAction(QAction("Time and weather…", self, triggered=self.time_weather, shortcut="Ctrl+W"))
        for text, line in (("Sunrise (06:00)", "time 6"), ("Noon (12:00)", "time 12"), ("Sunset (19:00)", "time 19"),
                           ("Midnight (00:00)", "time 0"), (None, None), ("Clear sky", "weather 0 0 0"),
                           ("Rain", "weather 70 80 80"), ("Release weather override", "weather off")):
            if text is None:
                w.addSeparator()
            else:
                w.addAction(QAction(text, self, triggered=lambda _=False, l=line: self.wrapper.send(l)))
        car = r.addMenu("&Car")
        self.animations_menu = car.addMenu("&Animations")
        self.animations_menu.aboutToShow.connect(self._animation_menu)
        scripts = mb.addMenu("&Scripts")
        scripts.addAction(QAction("Record / play XInput scripts...", self, triggered=self.wrapper.show_scripts))
        t = mb.addMenu("&Tools")
        t.addAction(QAction("Game files manager", self, triggered=self.open_game_files_manager))
        t.addAction(QAction("Console detach", self, triggered=lambda: self.wrapper.set_console_detached(True)))
        t.addAction(QAction("Console attach", self, triggered=lambda: self.wrapper.set_console_detached(False)))
        capture = t.addMenu("Map transition capture")
        capture.addAction(QAction("Start capture", self, triggered=self.wrapper.start_map_capture))
        capture.addAction(QAction("Stop and export...", self, triggered=self.wrapper.export_map_capture))
        t.addAction(QAction("Open file from disk…", self, triggered=self.open_disk_file))
        t.addAction(QAction("Open game logs (platform.log)", self, triggered=self.open_platform_log))
        h = mb.addMenu("&Help")
        h.addAction(QAction("How to", self, triggered=self.how_to))
        h.addAction(QAction("Credits", self, triggered=self.credits))
        h.addAction(QAction("About", self, triggered=lambda: QMessageBox.about(
            self, APP_NAME, "%s %s\nBuild car packs for Forza Horizon 2 (Azimuth)." % (APP_NAME, APP_VERSION))))

    # ----------------------------------------------------------- startup / settings
    def log(self, msg):
        self.output.appendPlainText(msg)

    def _startup(self):
        if not GameData.valid(self.s["mount"]):
            self.settings(first=True)
        self.load_game()
        lp = self.s.get("last_project")
        if lp and os.path.exists(lp):
            self._load_project(lp)

    def settings(self, first=False):
        if first:
            QMessageBox.information(self, APP_NAME, "Welcome! Select the game's 'Mount' folder (the folder that contains "
                                    "'media'). Game files are not shipped with this tool.")
        d = SettingsDialog(self.s, self)
        if d.exec():
            self.s.update(d.result_settings())
            config.save(self.s)
            self.wrapper.s = self.s
            self.load_game()

    def load_game(self):
        m = self.s["mount"]
        if not GameData.valid(m):
            self.statusBar().showMessage("Game Mount not set")
            return
        try:
            self.game = GameData(m)
            cars = self.game.load(self.s["language"])
            self.browser.load(cars)
            self.props.set_makes(self.game.makes)
            self.statusBar().showMessage("%d cars loaded from %s" % (len(cars), m))
            self.log("Loaded %d cars (%d from mods)" % (len(cars), sum(c.source == "mod" for c in cars)))
        except Exception as e:
            QMessageBox.warning(self, APP_NAME, "Cannot read the game data:\n%s" % e)

    # ----------------------------------------------------------- project
    def new_project(self):
        name, ok = QInputDialog.getText(self, "New pack", "Pack name (letters, digits, _):", text="MyCarPack")
        if not ok or not name.strip():
            return
        name = "".join(ch for ch in name if ch.isalnum() or ch in "_-") or "MyCarPack"
        base = os.path.join(os.path.expanduser("~"), "Documents", "CarPackStudio", name)
        d = QFileDialog.getExistingDirectory(self, "Project folder (a subfolder is created)", os.path.dirname(base))
        if not d:
            return
        path = os.path.join(d, name, "project.cpsproj")
        self.project = Project(name=name, path=path)
        self.project.save()
        self._project_loaded()

    def open_project(self):
        p, _ = QFileDialog.getOpenFileName(self, "Open project", "", "Car pack project (*.cpsproj)")
        if p:
            self._load_project(p)

    def _load_project(self, p):
        try:
            self.project = Project.load(p)
        except Exception as e:
            QMessageBox.warning(self, APP_NAME, "Cannot open: %s" % e)
            return
        self._project_loaded()

    def _project_loaded(self):
        self.s["last_project"] = self.project.path
        config.save(self.s)
        self.setWindowTitle("%s %s – %s" % (APP_NAME, APP_VERSION, self.project.name))
        self.refresh_pack_list()
        self.log("Project: %s" % self.project.path)

    def save_project(self):
        if self.project:
            self.project.save()
            self.statusBar().showMessage("Project saved", 3000)

    def project_changed(self):
        self.save_project()
        self.refresh_pack_list()

    def need_project(self):
        if not self.project:
            self.new_project()
        return self.project is not None

    def refresh_pack_list(self):
        self.pack_list.clear()
        if self.project:
            for c in self.project.cars:
                self.pack_list.addItem("%s %s  [%s]" % (c.year, c.name, c.media))

    def _pack_item_open(self, it):
        self._open_packcar(self.project.cars[self.pack_list.row(it)])

    def _pack_selected(self, row):
        if self.project and 0 <= row < len(self.project.cars):
            self.props.show_car(self.project.cars[row])
        else:
            self.props.show_car(None)

    def remove_car(self):
        r = self.pack_list.currentRow()
        if not self.project or not 0 <= r < len(self.project.cars):
            return
        car = self.project.cars[r]
        if QMessageBox.question(self, "Remove car from pack",
                                "Remove %s (ID %d) from this project?\n\n"
                                "Open tabs for this car will close, discarding unsaved edits. "
                                "Source/override files stay on disk. Installed copies are unchanged." %
                                (car.name, car.car_id), QMessageBox.Yes | QMessageBox.No,
                                QMessageBox.No) != QMessageBox.Yes:
            return
        for i in range(self.tabs.count() - 1, 0, -1):
            widget = self.tabs.widget(i)
            entry = widget.property("entry")
            if entry and entry[0] == car.media:
                self.tabs.removeTab(i)
                if isinstance(widget, BankViewer):
                    widget.shutdown()
                widget.deleteLater()
        if self.current and self.current.get("pack") is car:
            self.current = None
            if self.zip:
                self.zip.close()
                self.zip = None
            self.explorer.entries = []
            self.explorer.car = None
            self.explorer.title.setText("No car open")
            self.explorer.refill()
        del self.project.cars[r]
        self.project_changed()
        self.log("Removed %s from project; installed copies and source files were kept." % car.media)

    def _pack_menu(self, pos):
        item = self.pack_list.itemAt(pos)
        if item is None or self.project is None:
            return
        self.pack_list.setCurrentItem(item)
        menu = QMenu(self)
        menu.addAction("Remove car from pack...", self.remove_car)
        menu.addAction("Uninstall car from game database...", self.uninstall_selected_car)
        menu.exec(self.pack_list.viewport().mapToGlobal(pos))

    def uninstall_selected_car(self):
        row = self.pack_list.currentRow()
        if not self.project or not 0 <= row < len(self.project.cars):
            return
        if self.wrapper.rt.running():
            QMessageBox.warning(self, APP_NAME, "Stop the wrapper before uninstalling cars from the game database.")
            return
        car = self.project.cars[row]
        try:
            packs = [pack for pack in installed_packs(self._mods_dir())
                     if any(entry["id"] == car.car_id and entry["media"].casefold() == car.media.casefold()
                            for entry in pack.get("cars", []))]
            if not packs:
                QMessageBox.information(self, APP_NAME, "This car is not installed in the selected runtime.")
                return
            name = packs[0]["name"]
            if len(packs) > 1:
                name, ok = QInputDialog.getItem(self, "Installed pack", "Uninstall from:", [p["name"] for p in packs],
                                               0, False)
                if not ok:
                    return
            if QMessageBox.question(self, "Uninstall car",
                                    "Uninstall %s (ID %d) from '%s'?\n\n"
                                    "Close any other game instances first. Other pack cars and the base database "
                                    "are preserved. The car stays in this project; Install or Test in game will "
                                    "install it again. Changes take effect on the next launch." %
                                    (car.name, car.car_id, name), QMessageBox.Yes | QMessageBox.No,
                                    QMessageBox.No) != QMessageBox.Yes:
                return
            uninstall_car(name, car.car_id, self.s["mount"], self._mods_dir())
        except (OSError, ValueError, KeyError, sqlite3.Error) as ex:
            QMessageBox.critical(self, "Cannot uninstall car", str(ex))
            return
        self.log("Uninstalled car ID %d (%s) from %s. Project retained; reinstalling the pack restores it." %
                 (car.car_id, car.media, name))
        self.statusBar().showMessage("Car uninstalled from the game database", 5000)

    # ----------------------------------------------------------- cars
    def _car_title(self, media):
        key = media.casefold()
        car = next((c for c in (self.project.cars if self.project else []) if c.media.casefold() == key), None)
        if car:
            return "%s %s" % (car.year, car.name)
        car = next((c for c in (self.game.cars if self.game else []) if c.media.casefold() == key), None)
        if car:
            return car.title
        for pack in installed_packs(self._mods_dir()):
            for car in pack.get("cars", []):
                if car["media"].casefold() == key:
                    return car["name"]
        return media

    def import_car(self):
        if not self.game or not self.game.cars:
            QMessageBox.warning(self, APP_NAME, "Mount the game before importing a car; a physics/database donor is required.")
            return
        if not self.need_project():
            return
        path, _ = QFileDialog.getOpenFileName(self, "Import car into pack", "", "Car archive (*.zip)")
        if not path:
            return
        source = os.path.splitext(os.path.basename(path))[0]
        cars = self.game.cars
        labels = ["%s [%s, ID %d]" % (c.title, c.media, c.id) for c in cars]
        default = next((i for i, c in enumerate(cars) if c.media.casefold() == source.casefold()), 0)
        choice, ok = QInputDialog.getItem(self, "Car database donor",
                                         "ZIPs contain assets, not database rows. Choose the physics/data donor:",
                                         labels, default, False)
        if not ok:
            return
        donor = cars[labels.index(choice)]
        taken = {c.media.casefold() for c in cars} | {c.media.casefold() for c in self.project.cars}
        suggested = source if source.casefold() not in taken else source[:-3] + "CPS"
        media, ok = QInputDialog.getText(self, "Imported car", "New media name (same length as %s):" % source,
                                        text=suggested)
        if not ok:
            return
        media = media.strip()
        if media.casefold() in taken:
            QMessageBox.warning(self, APP_NAME, "That media name is already in use. Choose a unique name.")
            return
        used = {c.id for c in cars} | {c.car_id for c in self.project.cars}
        cid = 20000
        while cid in used:
            cid += 1
        car = PackCar(media=media, donor=donor.media, car_id=cid, year=donor.year,
                      make_id=donor.make_id, name=source, short=source)
        try:
            import_car_archive(self.project, car, path)
        except (OSError, ValueError, zipfile.BadZipFile, RuntimeError, NotImplementedError) as e:
            self.log("IMPORT FAILED: %s" % e)
            QMessageBox.warning(self, APP_NAME, "Cannot import car ZIP:\n%s" % e)
            return
        self.project_changed()
        self.pack_list.setCurrentRow(len(self.project.cars) - 1)
        self._open_packcar(car)
        self.log("Imported %s as %s (ID %d). Edit its name, brand and physics in Car properties." %
                 (path, media, cid))

    def add_clone(self, base):
        if not self.need_project():
            return
        media, ok = QInputDialog.getText(self, "New car", "Media name of the new car (zip name, exactly %d characters "
                                         "like the donor, no spaces):" % len(base.media),
                                         text=base.media[:-3] + "CPS")
        if not ok or not media.strip():
            return
        media = "".join(ch for ch in media if ch.isalnum() or ch in "_-")
        if len(media) != len(base.media):
            QMessageBox.warning(self, APP_NAME, "The media name must have exactly %d characters (the donor's length): "
                                "the car's internal texture/model paths are rewritten in place." % len(base.media))
            return
        used = {c.car_id for c in self.project.cars} | {c.id for c in self.game.cars}
        cid = 20000
        while cid in used:
            cid += 1
        car = PackCar(media=media, donor=base.media, car_id=cid, year=base.year, make_id=base.make_id,
                      name=base.name + " (copy)", short=base.short)
        self.project.cars.append(car)
        self.project_changed()
        self.pack_list.setCurrentRow(len(self.project.cars) - 1)
        self._open_packcar(car)

    def ensure_in_pack(self):
        """Editing a base car file requires cloning it into the pack first."""
        cur = self.current
        if cur["pack"]:
            return cur["pack"]
        base = next((c for c in self.game.cars if c.media == cur["media"]), None)
        if QMessageBox.question(self, APP_NAME, "'%s' is a base game car. Add a copy to the pack so you can edit it?"
                                % cur["media"]) != QMessageBox.Yes:
            return None
        self.add_clone(base)
        return self.current["pack"] if self.current else None

    def open_car(self, car):
        zp = car.zip_path
        pc = next((p for p in (self.project.cars if self.project else []) if p.media == car.media), None)
        if pc:
            return self._open_packcar(pc)
        self._load_zip(car.media, zp, None, car.title)

    def _open_packcar(self, pc):
        dz = os.path.join(self.s["mount"], "media", "cars", pc.donor + ".zip")
        if not os.path.exists(dz):
            dz = os.path.join(self.s["mount"], "mediapc", "cars", pc.donor + ".zip")
        dz = car_archive_path(self.project, pc, dz)
        self._load_zip(pc.media, dz, pc, "%s %s" % (pc.year, pc.name))
        self.wrapper.set_car(pc.media, pc.name)
        self.props.show_car(pc)

    def _load_zip(self, media, zip_path, pack, title):
        if self.zip:
            self.zip.close()
        self.zip = zipfile.ZipFile(zip_path)
        names = {n for n in self.zip.namelist() if not n.endswith("/")}
        over = set()
        if pack and self.project:
            folder = pack.folder(self.project.dir)
            if os.path.isdir(folder):
                for root, _d, files in os.walk(folder):
                    for f in files:
                        rel = os.path.relpath(os.path.join(root, f), folder).replace("\\", "/")
                        names.add(rel)
                        over.add(rel)
        self.current = {"media": media, "zip": zip_path, "pack": pack, "title": title}

        class T:
            pass
        t = T()
        t.title = title
        self.explorer.set_car(t, names, over, pack is not None)
        self.wrapper.set_car(media, title)
        self.statusBar().showMessage("Opened %s (%d files)" % (media, len(names)))

    # ----------------------------------------------------------- files
    def read_entry(self, name):
        cur = self.current
        pack = cur["pack"]
        if pack and self.project:
            p = os.path.join(pack.folder(self.project.dir), name.replace("/", os.sep))
            if os.path.exists(p):
                with open(p, "rb") as f:
                    return f.read()
        return self.zip.read(name)

    def open_file(self, name):
        if not self.current:
            return
        for i in range(self.tabs.count()):
            if self.tabs.widget(i).property("entry") == (self.current["media"], name):
                self.tabs.setCurrentIndex(i)
                return
        try:
            data = self.read_entry(name)
        except KeyError:
            return
        short = name.rsplit("/", 1)[-1]
        try:
            w = self._asset_widget(name, data, self._save_text,
                                   "" if self.current["pack"] else "read-only until the car is added to the pack")
        except (OSError, ValueError) as ex:
            QMessageBox.warning(self, "Cannot open asset", "%s\n%s" % (name, ex))
            return
        w.setProperty("entry", (self.current["media"], name))
        i = self.tabs.addTab(w, short)
        self.tabs.setTabToolTip(i, name)
        self.tabs.setCurrentIndex(i)

    @staticmethod
    def _hex(data, limit=65536):
        out = []
        for off in range(0, min(len(data), limit), 16):
            ch = data[off:off + 16]
            out.append("%08X  %-47s  %s" % (off, " ".join("%02X" % b for b in ch),
                                           "".join(chr(b) if 32 <= b < 127 else "." for b in ch)))
        if len(data) > limit:
            out.append("… (%d more bytes)" % (len(data) - limit))
        return "\n".join(out)

    def _save_text(self, name, text):
        pack = self.ensure_in_pack()
        if not pack:
            return
        self.write_override(pack, name, text.encode("utf-8"))

    def write_override(self, pack, name, data):
        p = os.path.join(pack.folder(self.project.dir), name.replace("/", os.sep))
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as f:
            f.write(data)
        self.explorer.overridden.add(name)
        self.explorer.entries = sorted(set(self.explorer.entries) | {name})
        self.explorer.editable = True
        self.explorer.refill()
        self.statusBar().showMessage("Saved %s into the project" % name, 4000)

    def export_file(self, name):
        p, _ = QFileDialog.getSaveFileName(self, "Export", name.rsplit("/", 1)[-1])
        if p:
            with open(p, "wb") as f:
                f.write(self.read_entry(name))

    def replace_file(self, name):
        p, _ = QFileDialog.getOpenFileName(self, "Replace %s with…" % name)
        if not p:
            return
        pack = self.ensure_in_pack()
        if pack:
            with open(p, "rb") as f:
                self.write_override(pack, name, f.read())

    def revert_file(self, name):
        pack = self.current["pack"]
        p = os.path.join(pack.folder(self.project.dir), name.replace("/", os.sep))
        if os.path.exists(p):
            os.remove(p)
        self.explorer.overridden.discard(name)
        if name not in self.zip.namelist():
            self.explorer.entries = [e for e in self.explorer.entries if e != name]
        self.explorer.refill()

    def open_disk_file(self):
        p, _ = QFileDialog.getOpenFileName(self, "Open file", self.s.get("mount", ""), FILE_FILTER)
        if p:
            if p.lower().endswith(".zip"):
                manager = self.open_game_files_manager()
                manager.load_archive(p)
            else:
                self.open_asset_path(p)

    @staticmethod
    def _asset_widget(name, data, save_cb=None, hint=""):
        low = name.lower()
        if low.endswith(".swatchbin"):
            return SwatchViewer(name, data)
        if low.endswith(".modelbin"):
            return ModelViewer(name, data)
        if low.endswith(".bank"):
            return BankViewer(name, data)
        if low.endswith(TEXT_EXT) and not data.startswith(b"LDFB"):
            if data.startswith((b"\xff\xfe", b"\xfe\xff")):
                text = data.decode("utf-16")
            elif b"\0" in data[:1024]:
                return BinaryViewer(name, data)
            else:
                text = data.decode("utf-8-sig", "replace")
            widget = TextEditorTab(name, text, kind="xml" if low.endswith(".avpins") else None,
                                   save_cb=save_cb, hint=hint)
            if save_cb is None:
                widget.editor.setReadOnly(True)
                widget.save_btn.setEnabled(False)
            return widget
        return BinaryViewer(name, data)

    def open_game_files_manager(self):
        for i in range(self.tabs.count()):
            widget = self.tabs.widget(i)
            if isinstance(widget, GameFilesManager):
                widget.set_root(self.s.get("mount", ""))
                self.tabs.setCurrentIndex(i)
                return widget
        widget = GameFilesManager(self.s.get("mount", ""))
        widget.open_requested.connect(self.open_asset_path)
        self.tabs.setCurrentIndex(self.tabs.addTab(widget, "Game files manager"))
        return widget

    def open_asset_path(self, path, entry=None):
        key = (os.path.normcase(os.path.abspath(path)), entry)
        for i in range(self.tabs.count()):
            if self.tabs.widget(i).property("asset_path") == key:
                self.tabs.setCurrentIndex(i)
                return
        try:
            if entry is not None:
                with zipfile.ZipFile(path) as archive:
                    data = archive.read(entry)
                name = entry
                save = None
            else:
                with open(path, "rb") as f:
                    data = f.read()
                name = os.path.basename(path)

                def save(_name, text):
                    try:
                        with open(path, "w", encoding="utf-8") as f:
                            f.write(text)
                    except OSError as ex:
                        QMessageBox.critical(self, "Cannot save file", str(ex))
                        raise
            widget = self._asset_widget(name, data, save, "archive entry (read-only)" if entry else "on-disk file")
        except (OSError, ValueError, KeyError, zipfile.BadZipFile) as ex:
            QMessageBox.warning(self, "Cannot open asset", "%s%s\n%s" % (path, " :: " + entry if entry else "", ex))
            return
        widget.setProperty("asset_path", key)
        i = self.tabs.addTab(widget, name.replace("\\", "/").rsplit("/", 1)[-1])
        self.tabs.setTabToolTip(i, path + (" :: " + entry if entry else ""))
        self.tabs.setCurrentIndex(i)

    def open_platform_log(self):
        p = self.wrapper.rt.log_files()["platform"]
        if os.path.exists(p):
            with open(p, "rb") as f:
                text = f.read().decode("utf-8", "replace")
            i = self.tabs.addTab(TextEditorTab("platform.log", text, save_cb=lambda n, t: None), "platform.log")
            self.tabs.setCurrentIndex(i)
        else:
            self.statusBar().showMessage("platform.log not found yet – run the game once", 4000)

    def _close_tab(self, i):
        if i == 0:
            return
        w = self.tabs.widget(i)
        if isinstance(w, TextEditorTab) and w.editor.document().isModified():
            if QMessageBox.question(self, APP_NAME, "Discard unsaved changes?") != QMessageBox.Yes:
                return
        self.tabs.removeTab(i)
        if isinstance(w, BankViewer):
            w.shutdown()
        w.deleteLater()

    # ----------------------------------------------------------- pack
    def _mods_dir(self):
        return self.wrapper.rt.mods_dir

    def build(self, quiet=False):
        if not self.project or not self.project.cars:
            QMessageBox.information(self, APP_NAME, "Add at least one car to the pack first (Cars tab → right-click → clone).")
            return None
        out = os.path.join(self.project.dir, "build")
        shutil.rmtree(out, ignore_errors=True)
        try:
            meta = build_pack(self.project, self.s["mount"], out)
        except Exception as e:
            self.log("BUILD FAILED: %s" % e)
            QMessageBox.warning(self, APP_NAME, "Build failed:\n%s" % e)
            return None
        self.log("Built %s: %s" % (self.project.name, ", ".join(c["media"] for c in meta["cars"])))
        return out

    def _sync_extra_cars(self):
        ids = {c.media.lower(): c.id for c in (self.game.cars if self.game else [])}
        rt = self.wrapper.rt
        rt.s = self.s
        rt.extra_cars = [(c.car_id, ids[c.donor.lower()]) for c in (self.project.cars if self.project else [])
                         if c.donor.lower() in ids]

    def install(self):
        out = self.build()
        if not out:
            return False
        self._sync_extra_cars()
        self.wrapper.rt.prepare(self.log)
        name = install_pack(out, self.s["mount"], self._mods_dir())
        self.log("Installed pack '%s' → %s and Mount\\mediapc" % (name, self._mods_dir()))
        self.statusBar().showMessage("Pack installed", 4000)
        return True

    def _prepare_for_run(self, write):
        if self.project and self.project.cars:
            out = os.path.join(self.project.dir, "build")
            shutil.rmtree(out, ignore_errors=True)
            build_pack(self.project, self.s["mount"], out)
            self._sync_extra_cars()
            self.wrapper.rt.prepare(write)
            install_pack(out, self.s["mount"], self._mods_dir())
            write("pack '%s' installed for this run" % self.project.name)
            self._preflight(out, write)
        else:
            self._sync_extra_cars()
            self.wrapper.rt.prepare(write)

    def _preflight(self, out, write):
        """Reports everything that can make the game fall back to null_car_00 for the pack's cars."""
        try:
            res = check_pack(self.project, self.s["mount"], out, self._mods_dir())
        except Exception as e:
            write("pre-flight check failed: %s" % e)
            return
        lines = ["[%s] %s" % lv_t for lv_t in res]
        for ln in lines:
            write("CHECK " + ln)
        bad = sum(lv == "ERROR" for lv, _ in res)
        if bad:
            write("CHECK %d error(s): these cars will most likely appear as null_car_00 in game" % bad)
        with open(os.path.join(self.project.dir, "diagnostics.log"), "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")

    def _after_run(self, write):
        if not (self.project and self.project.cars):
            return
        rt = self.wrapper.rt
        hits = scan_logs(list(rt.log_files().values()), [c.media for c in self.project.cars])
        for h in hits[-30:]:
            write("LOG " + h)
        if any("null_car" in h.lower() for h in hits):
            write("LOG null_car_00 was used by the game - see the CHECK lines above for what is missing in the pack")
    def _selected_car_id(self):
        r = self.pack_list.currentRow()
        if self.project and 0 <= r < len(self.project.cars):
            return self.project.cars[r].car_id
        cur = self.current
        if cur:
            pc = next((c for c in (self.project.cars if self.project else []) if c.media == cur["media"]), None)
            if pc:
                return pc.car_id
            base = next((c for c in self.game.cars if c.media == cur["media"]), None)
            if base:
                return base.id
        return self.project.cars[0].car_id if self.project and self.project.cars else None

    def test_in_game(self):
        self.tabs.setCurrentIndex(0)
        r = self.pack_list.currentRow()
        if self.project and 0 <= r < len(self.project.cars):
            c = self.project.cars[r]
            return self._test_car(c.media, c.name, c.car_id)
        cur = self.current
        if cur:
            base = next((c for c in self.game.cars if c.media == cur["media"]), None)
            if base:
                return self._test_car(base.media, base.title, base.id)
        self.wrapper.start()

    def test_base_car(self, car):
        self.tabs.setCurrentIndex(0)
        self._test_car(car.media, car.title, car.id)

    def _test_car(self, media, name, car_id):
        w = self.wrapper
        w.set_car(media, name)
        if w.rt.running():
            w.send("car %d" % car_id)
        else:
            w.forced_car = car_id
            w.start()

    def _animation_menu(self):
        menu = self.animations_menu
        menu.clear()
        telemetry = (self.wrapper.rt.telemetry() or {}) if self.wrapper.rt.running() else {}
        media = telemetry.get("car") if telemetry.get("valid") else None
        if not media:
            menu.addAction("Start the wrapper and load a car first").setEnabled(False)
            return
        response = self.wrapper.rt.command("CarPackStudio animations") or ""
        if response.startswith("error:") or not response:
            menu.addAction(response or "Animation graph unavailable").setEnabled(False)
            self.log("Animations: " + (response or "animation graph unavailable"))
            return
        states = set(response.splitlines())
        car = next((c for c in self.game.cars if c.media == media), None)
        paths = [os.path.join(self.s["mount"], d, "cars", media + ".zip")
                 for d in ("mediaoverride", "mediapc", "media")]
        if car:
            paths.insert(0, car.zip_path)
        path = next((p for p in paths if os.path.isfile(p)), None)
        if not path:
            menu.addAction("Car archive not found: " + media).setEnabled(False)
            self.log("Animations: car archive not found for " + media)
            return
        try:
            with zipfile.ZipFile(path) as z:
                names = sorted(n for n in z.namelist()
                               if n.lower().startswith("animations/") and n.lower().endswith(".gr2"))
        except (OSError, zipfile.BadZipFile) as e:
            menu.addAction("Cannot read animations").setEnabled(False)
            self.log("Animations: %s" % e)
            return
        menu.addAction(media).setEnabled(False)
        menu.addSeparator()
        shown = set()
        for name in names:
            stem = os.path.splitext(os.path.basename(name))[0]
            part, separator, transition = stem.rpartition("_")
            state = part + ":" + transition if separator and transition in ("open", "close") else stem
            action = menu.addAction(os.path.basename(name))
            playable = state in states
            action.setEnabled(playable)
            action.setToolTip("Play state " + state if playable else "Clip has no independently playable state in this car's GSF")
            action.triggered.connect(lambda _=False, s=state: self.wrapper.send("animation " + s))
            if playable:
                shown.add(state)
        remaining = states - shown
        if remaining:
            menu.addSeparator()
            for state in sorted(remaining):
                action = menu.addAction(state)
                action.triggered.connect(lambda _=False, s=state: self.wrapper.send("animation " + s))
        if not names:
            menu.addAction("No animation clips in this car archive").setEnabled(False)

    def time_weather(self):
        dlg = QDialog(self)
        dlg.setWindowTitle("Time and weather")
        lay = QFormLayout(dlg)
        sliders = {}

        def add(label, lo, hi, val, fmt):
            from PySide6.QtWidgets import QSlider
            s = QSlider(Qt.Horizontal)
            s.setRange(lo, hi)
            s.setValue(val)
            v = QLabel(fmt(val))
            s.valueChanged.connect(lambda x: v.setText(fmt(x)))
            row = QHBoxLayout()
            row.addWidget(s, 1)
            row.addWidget(v)
            lay.addRow(label, row)
            sliders[label] = s
        add("Time of day", 0, 1439, 12 * 60, lambda m: "%02d:%02d" % (m // 60, m % 60))
        freeze = QCheckBox("Freeze time")
        lay.addRow("", freeze)
        add("Atmosphere (clouds)", 0, 100, 0, lambda x: "%d%%" % x)
        add("Rain", 0, 100, 0, lambda x: "%d%%" % x)
        add("Environment (wet/storm)", 0, 100, 0, lambda x: "%d%%" % x)
        w = self.wrapper

        def apply_time():
            m = sliders["Time of day"].value()
            w.send("time %.4f" % (m / 60.0))
            w.send("freeze %d" % (1 if freeze.isChecked() else 0))

        def apply_weather():
            w.send("weather %d %d %d" % (sliders["Atmosphere (clouds)"].value(), sliders["Rain"].value(),
                                         sliders["Environment (wet/storm)"].value()))
        row = QHBoxLayout()
        for text, fn in (("Apply time", apply_time), ("Apply weather", apply_weather),
                         ("Release weather", lambda: w.send("weather off")), ("Close", dlg.accept)):
            b = QPushButton(text)
            b.clicked.connect(fn)
            row.addWidget(b)
        lay.addRow(row)
        dlg.exec()

    def uninstall(self):
        packs = installed_packs(self._mods_dir())
        if not packs:
            QMessageBox.information(self, APP_NAME, "No packs installed.")
            return
        names = [p["name"] for p in packs]
        n, ok = QInputDialog.getItem(self, "Uninstall", "Pack:", names, 0, False)
        if ok:
            uninstall_pack(n, self.s["mount"], self._mods_dir())
            self.log("Uninstalled %s" % n)

    def show_sql(self):
        if not self.project or not self.project.cars:
            return
        sql, strings = generate_sql(self.project, os.path.join(self.s["mount"], "media", "stripped", "gamedbRC.slt"))
        w = TextEditorTab("pack.sql", sql + "\n-- strings\n" + "\n".join("-- " + x for x in strings), kind="txt",
                          save_cb=lambda n, t: None)
        w.editor.setReadOnly(True)
        self.tabs.setCurrentIndex(self.tabs.addTab(w, "pack.sql"))

    def export_zip(self):
        out = self.build()
        if not out:
            return
        p, _ = QFileDialog.getSaveFileName(self, "Export pack", "%s-%s.zip" % (self.project.name, self.project.version), "Zip (*.zip)")
        if not p:
            return
        with zipfile.ZipFile(p, "w", zipfile.ZIP_DEFLATED) as z:
            for root, _d, files in os.walk(out):
                for f in files:
                    full = os.path.join(root, f)
                    z.write(full, os.path.relpath(full, out))
            z.writestr("README.txt", "%s %s by %s\r\n\r\nCopy 'mediapc' into <game>\\Mount and 'CarPacks' into "
                       "azimuth\\fh2-mods (needs CarPackLoader.dll from Car Pack Studio, FH2 Mod Platform 2.8.0 and "
                       "Extra Options with EnableMediaOverrides=1). Merge your zipmanifest.xml lines from "
                       "CarPacks\\%s\\pack.json.\r\n" % (self.project.name, self.project.version, self.project.author,
                                                         self.project.name))
        self.log("Exported %s" % p)

    def credits(self):
        d = QDialog(self)
        d.setWindowTitle("Credits")
        v = QVBoxLayout(d)
        tb = QTextBrowser()
        tb.setHtml(CREDITS)
        v.addWidget(tb)
        d.resize(620, 520)
        d.exec()

    def how_to(self):
        path = os.path.join(config.ROOT, "resources", "how-to.html")
        try:
            with open(path, encoding="utf-8") as f:
                guide = f.read()
        except OSError as ex:
            QMessageBox.critical(self, "How to", "Cannot read the bundled guide:\n%s" % ex)
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("How to - Car Pack Studio 0.1.0 Experimental")
        dialog.resize(1000, 780)
        dialog.setMinimumSize(640, 480)
        dialog.setSizeGripEnabled(True)
        layout = QVBoxLayout(dialog)
        browser = QTextBrowser()
        browser.setOpenExternalLinks(True)
        browser.setHtml(guide)
        layout.addWidget(browser)
        close = QPushButton("Close")
        close.clicked.connect(dialog.accept)
        layout.addWidget(close)
        dialog.exec()

    def closeEvent(self, e):
        for i in range(self.tabs.count()):
            widget = self.tabs.widget(i)
            if isinstance(widget, BankViewer):
                widget.shutdown()
        self.wrapper.shutdown()
        config.save(self.s)
        super().closeEvent(e)


def main():
    install_gl_format()
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("CarPackStudio.Desktop")
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    icon = QIcon(os.path.join(config.ROOT, "resources", "app.ico"))
    if icon.isNull():
        raise FileNotFoundError("Bundled resources\\app.ico is missing or invalid")
    app.setWindowIcon(icon)
    theme.apply(app)
    w = MainWindow()
    w.show()
    sys.exit(app.exec())
