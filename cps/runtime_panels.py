"""Searchable runtime browsers sharing the wrapper's existing command interface."""
import re
import os
import sqlite3
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import Qt, QSortFilterProxyModel
from PySide6.QtGui import QStandardItem, QStandardItemModel
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLineEdit, QPushButton, QLabel,
                               QTreeView, QHeaderView, QMenu, QFileDialog, QMessageBox)

from . import theme
from .gamedata import GameData
from .pack import installed_packs
from .runtime_state import parse_spawned
from .travel import TRACKS, load_places, track_label
from .travel_ui import populate_places
from .dependencies import missing_dependencies, import_dependencies, OPTIONAL
from . import config


class BrowserPanel(QWidget):
    def __init__(self, owner, headers):
        super().__init__()
        self.owner = owner
        self.wrapper = owner.wrapper
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search...")
        self.refresh_button = QPushButton("Refresh")
        self.refresh_button.clicked.connect(self.refresh)
        self.model = QStandardItemModel(0, len(headers))
        self.model.setHorizontalHeaderLabels(headers)
        self.proxy = QSortFilterProxyModel(self)
        self.proxy.setSourceModel(self.model)
        self.proxy.setFilterCaseSensitivity(Qt.CaseInsensitive)
        self.proxy.setFilterKeyColumn(-1)
        self.proxy.setRecursiveFilteringEnabled(True)
        self.proxy.setAutoAcceptChildRows(True)
        self.search.textChanged.connect(self.proxy.setFilterFixedString)
        self.tree = QTreeView()
        self.tree.setModel(self.proxy)
        self.tree.setSortingEnabled(True)
        self.tree.setRootIsDecorated(False)
        self.tree.header().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.tree.header().setStretchLastSection(True)
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self.context_menu)
        self.note = QLabel()
        self.note.setWordWrap(True)
        self.note.setStyleSheet("color:%s; padding:2px;" % theme.FG_DIM)
        top = QHBoxLayout()
        top.addWidget(self.search, 1)
        top.addWidget(self.refresh_button)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addLayout(top)
        layout.addWidget(self.tree)
        layout.addWidget(self.note)

    def add_row(self, values, data=None, parent=None):
        items = []
        for value in values:
            item = QStandardItem()
            item.setData(value, Qt.DisplayRole)
            item.setEditable(False)
            items.append(item)
        items[0].setData(data, Qt.UserRole)
        (parent if parent is not None else self.model.invisibleRootItem()).appendRow(items)
        return items[0]

    def clear(self):
        self.model.removeRows(0, self.model.rowCount())

    def at(self, index):
        return index.siblingAtColumn(0).data(Qt.UserRole) if index.isValid() else None

    def refresh(self):
        raise NotImplementedError

    def menu_for(self, data):
        raise NotImplementedError

    def context_menu(self, pos):
        data = self.at(self.tree.indexAt(pos))
        if data is not None:
            self.menu_for(data).exec(self.tree.viewport().mapToGlobal(pos))

    def runtime_action(self, menu, text, callback, enabled=True):
        action = menu.addAction(text, callback)
        action.setEnabled(self.wrapper.rt.running() and enabled)
        if not self.wrapper.rt.running():
            action.setToolTip("Start the wrapper first.")
        return action

    def report_error(self, message):
        self.note.setText(message)
        self.owner.log(message)
        self.wrapper.console.notice(message, theme.RED)


class MapsPanel(BrowserPanel):
    def __init__(self, owner):
        super().__init__(owner, ["Map ID", "Map"])
        self.tree.doubleClicked.connect(lambda index: self.travel(self.at(index)))
        self.wrapper.places_changed.connect(self.refresh)

    def refresh(self):
        self.clear()
        try:
            ids = set(TRACKS.values()) | {place.track_id for place in load_places()}
            for track_id in sorted(ids):
                self.add_row([track_id, track_label(track_id)], track_id)
            self.note.setText("Right-click for travel or teleport. Storm Island requires mounted DLC.\n"
                              "Custom map bookmarks are listed; native travel currently supports 542 and 549.")
        except (OSError, ValueError) as ex:
            self.report_error("Cannot read maps: %s" % ex)

    def travel(self, track_id, place=None):
        if track_id is not None:
            if place is None:
                self.wrapper.navigate(track_id)
            else:
                self.wrapper.teleport_to(track_id, place)

    def menu_for(self, track_id):
        menu = QMenu(self)
        supported = track_id in TRACKS.values()
        self.runtime_action(menu, "Change map", lambda: self.travel(track_id), supported)
        teleports = menu.addMenu("Teleport")
        teleports.setEnabled(self.wrapper.rt.running() and supported)
        populate_places(teleports, self.wrapper, track_id)
        return menu


class EventsPanel(BrowserPanel):
    def __init__(self, owner):
        super().__init__(owner, ["Event ID", "Event", "Route", "Laps"])
        self.tree.setRootIsDecorated(True)
        self.stop_button = QPushButton("Event stop")
        self.stop_button.setToolTip("Send event stop to return a Studio-started running race to free roam.")
        self.stop_button.setEnabled(self.wrapper.rt.running())
        self.stop_button.clicked.connect(lambda: self.wrapper.send("event stop"))
        self.wrapper.running_changed.connect(self.stop_button.setEnabled)
        self.layout().addWidget(self.stop_button)

    def refresh(self):
        self.clear()
        if not GameData.valid(self.owner.s.get("mount", "")):
            self.note.setText("Mount the game to list supported standard races.")
            return
        try:
            races = GameData(self.owner.s["mount"]).event_races(self.owner.s.get("language", "EN"))
            for name, track_id in TRACKS.items():
                parent = self.add_row([track_id, name, "", ""])
                for race in races:
                    if race.map_name == name:
                        self.add_row([race.id, race.name, race.route, race.laps], race, parent)
            self.tree.expandAll()
            self.note.setText("%d standard races. Right-click to start; normal save/progression rules apply." % len(races))
        except (OSError, ValueError, sqlite3.Error, zipfile.BadZipFile) as ex:
            self.report_error("Cannot read events: %s" % ex)

    def menu_for(self, race):
        menu = QMenu(self)
        self.runtime_action(menu, "Start event",
                            lambda: self.wrapper.navigate(TRACKS[race.map_name], "event start %d" % race.id))
        return menu


class SpawnedPanel(BrowserPanel):
    def __init__(self, owner):
        super().__init__(owner, ["Object ID", "Type", "Car ID", "AI", "State"])
        self.available = False
        self.last_error = None
        self.last_reply = None
        self.reset = QPushButton("Return to player car")
        self.clear_button = QPushButton("Clear all")
        self.reset.clicked.connect(lambda: self.wrapper.send("spawn swap reset"))
        self.clear_button.clicked.connect(lambda: self.wrapper.send("spawn clear"))
        buttons = QHBoxLayout()
        buttons.addWidget(self.reset)
        buttons.addWidget(self.clear_button)
        self.layout().addLayout(buttons)
        self.wrapper.native_snapshot.connect(self.on_snapshot)
        self.wrapper.running_changed.connect(self.running_changed)
        self.running_changed(False)

    def running_changed(self, running):
        self.available = False
        self.last_reply = None
        self.last_error = None
        self.reset.setEnabled(running)
        self.clear_button.setEnabled(running)
        if not running:
            self.clear()
            self.note.setText("Wrapper stopped. Only Studio-owned objects are tracked.")

    def refresh(self):
        self.wrapper.poller.refresh_native = True
        if not self.wrapper.rt.running():
            self.running_changed(False)

    def on_snapshot(self, snapshot):
        try:
            objects = parse_spawned(snapshot["spawn"])
        except ValueError as ex:
            self.available = False
            message = str(ex)
            self.note.setText(message + "\nLast successful rows may be stale; actions disabled.")
            error_key = "connection" if snapshot["spawn"].startswith("error:") else message
            if self.last_error != error_key:
                self.owner.log(message)
                self.wrapper.console.notice(message, theme.RED)
            self.last_error = error_key
            self.reset.setEnabled(False)
            self.clear_button.setEnabled(False)
            return
        self.available = True
        if self.last_error:
            self.wrapper.console.notice("Mod platform connection restored; spawned objects refreshed.", theme.GREEN)
        self.last_error = None
        self.reset.setEnabled(self.wrapper.rt.running())
        self.clear_button.setEnabled(self.wrapper.rt.running())
        self.note.setText("%d / 4 objects. Spawn one car at a time; AI starts paused." % len(objects))
        if self.last_reply == snapshot["spawn"]:
            return
        selected = self.at(self.tree.currentIndex())
        self.clear()
        for obj in objects:
            item = self.add_row([obj.id, "AI car" if obj.ai != "none" else obj.kind, obj.car_id,
                                obj.ai, obj.label], obj)
            if selected and selected.id == obj.id:
                self.tree.setCurrentIndex(self.proxy.mapFromSource(item.index()))
        self.last_reply = snapshot["spawn"]

    def menu_for(self, obj):
        menu = QMenu(self)
        enabled = self.available and not obj.removing
        self.runtime_action(menu, "Delete", lambda: self.wrapper.send("spawn remove %s %d" % (obj.kind, obj.id)), enabled)
        self.runtime_action(menu, "Swap to this car", lambda: self.wrapper.send("spawn swap %d" % obj.id),
                            enabled and obj.can_swap)
        self.runtime_action(menu, "Return to player car", lambda: self.wrapper.send("spawn swap reset"),
                            enabled and obj.possessed)
        ai = menu.addMenu("AI behaviour")
        ai.setEnabled(self.wrapper.rt.running() and enabled and obj.ai != "none" and not obj.possessed)
        for label, command in (("Pause", "pause"), ("Play", "play"), ("Reset", "reset")):
            ai.addAction(label, lambda checked=False, c=command: self.wrapper.send("drivatar %s %d" % (c, obj.id)))
        for label in ("Follow (not available yet)", "Chase (not available yet)"):
            ai.addAction(label).setEnabled(False)
        return menu


@dataclass(frozen=True)
class InstalledMod:
    name: str
    version: str
    source: str
    configs: tuple[Path, ...]


def mod_inventory(runtime) -> list[InstalledMod]:
    """Read files/session metadata only; never load a mod DLL into the editor."""
    root = Path(runtime.dir)
    mods = root / "fh2-mods"
    session = runtime.session() or {}
    if not isinstance(session, dict):
        raise ValueError("Invalid platform-session.json")
    versions = session.get("mods", {})
    if not isinstance(versions, dict):
        raise ValueError("Invalid mod versions in platform-session.json")
    live = runtime.running() and session.get("pid") == runtime.proc.pid
    source = "Loaded" if live else "Installed / last-session version"

    def normalize(name):
        return re.sub(r"[^a-z0-9]", "", name.casefold())

    aliases = {"fh2mouse": "FH2 Mouse & Keyboard", "fh2nopause": "NoPause"}
    by_name = {normalize(name): (name, str(version)) for name, version in versions.items()}
    ini_files = list(mods.rglob("*.ini")) if mods.is_dir() else []
    rows = []
    for dll in sorted(mods.glob("*.dll"), key=lambda path: path.name.casefold()):
        key = normalize(dll.stem)
        metadata_key = normalize(aliases.get(key, dll.stem))
        name, version = by_name.get(metadata_key, (dll.stem, "Unknown (not reported)"))
        configs = [path for path in ini_files if normalize(path.stem) in (key, normalize(name))]
        mouse_ini = root / "FH2Mouse" / "FH2Mouse.ini"
        if key == "fh2mouse" and mouse_ini.is_file():
            configs.append(mouse_ini)
        rows.append(InstalledMod(name, version, source if metadata_key in by_name else "Installed", tuple(configs)))
    if (root / "DWrite.dll").is_file():
        rows.insert(0, InstalledMod("FH2 Mod Platform", str(session.get("platform", "Unknown (not reported)")),
                                    source, ()))
    extra_ini = root / "FH2_ExtraOptions.ini"
    if (root / "winmm.dll").is_file() and extra_ini.is_file():
        text = extra_ini.read_text(encoding="utf-8-sig")
        match = re.search(r"(?im)^;\s*Version\s+v?([0-9.]+)", text)
        rows.append(InstalledMod("FH2 Extra Options", match[1] if match else "Unknown (not reported)",
                                 "Installed", (extra_ini,)))
    for pack in installed_packs(str(mods)):
        rows.append(InstalledMod(pack["name"], str(pack.get("version", "Unknown (not reported)")), "Car pack", ()))
    return rows


class ModsPanel(BrowserPanel):
    def __init__(self, owner):
        super().__init__(owner, ["Mod", "Version", "Status"])
        self.last_refresh = 0
        self.session_pid = None
        self.wrapper.running_changed.connect(self.running_changed)
        self.wrapper.native_snapshot.connect(self.on_snapshot)
        buttons = QHBoxLayout()
        self.open_button = QPushButton("Open Studio Mods")
        self.open_button.clicked.connect(self.open_mods)
        self.import_button = QPushButton("Import dependencies...")
        self.import_button.clicked.connect(self.import_from_folder)
        buttons.addWidget(self.open_button)
        buttons.addWidget(self.import_button)
        self.layout().insertLayout(1, buttons)
        self.import_button.setEnabled(not self.wrapper.rt.running())

    def running_changed(self, running):
        self.session_pid = None
        self.import_button.setEnabled(not running)
        self.refresh()

    def open_mods(self):
        try:
            directory = Path(self.wrapper.rt.mods_dir)
            directory.mkdir(parents=True, exist_ok=True)
            os.startfile(str(directory))
        except OSError as ex:
            self.wrapper.console.notice("error: cannot open Studio mods: %s" % ex, theme.RED)

    def import_from_folder(self):
        if self.wrapper.rt.running():
            self.wrapper.console.notice("error: stop the wrapper before importing dependencies.", theme.RED)
            return
        source = QFileDialog.getExistingDirectory(self, "Select installed Azimuth folder",
                                                  self.wrapper.s.get("dependency_source", ""))
        if not source:
            return
        if QMessageBox.question(self, "Import runtime dependencies",
                                "Copy Azimuth, FH2 Mod Platform and Extra Options into:\n%s\n\n"
                                "Installed ShowFPS, FH2 Mouse & Keyboard and NoPause will also be copied.\n"
                                "Existing INI/DAT settings are kept. Saves, caches and unknown mods are not copied.\n"
                                "Close games using either folder before continuing." % self.wrapper.rt.dir) != QMessageBox.Yes:
            return
        try:
            import_dependencies(source, self.wrapper.rt.dir, self.wrapper.console.notice)
            self.wrapper.s["dependency_source"] = source
            config.save(self.wrapper.s)
            self.wrapper.refresh_optional_mods()
            self.refresh()
        except (OSError, ValueError) as ex:
            self.wrapper.console.notice("error: dependency import failed: %s" % ex, theme.RED)
            QMessageBox.warning(self, "Dependency import failed", str(ex))

    def on_snapshot(self, snapshot):
        if snapshot["spawn"].startswith("Studio spawned cars:") and self.session_pid != snapshot["pid"]:
            self.session_pid = snapshot["pid"]
            self.refresh()

    def showEvent(self, event):
        super().showEvent(event)
        if time.monotonic() - self.last_refresh > 2:
            self.refresh()

    def refresh(self):
        self.clear()
        self.last_refresh = time.monotonic()
        try:
            rows = mod_inventory(self.wrapper.rt)
            for mod in rows:
                self.add_row([mod.name, mod.version, mod.source], mod)
            self.note.setText("%d installed mods/packs. INI changes generally apply on the next launch.\n"
                              "Studio, telemetry port and FPS toggles are managed by wrapper settings.\n"
                              "Offline versions come from the last platform session; unreported versions are marked unknown."
                              % len(rows))
            missing = missing_dependencies(self.wrapper.rt.dir)
            optional = [name for name, relative in OPTIONAL.items()
                        if not (Path(self.wrapper.rt.dir) / relative).is_file()]
            self.note.setText(self.note.text() + "\n" +
                              ("Required dependencies missing:\n" + "\n".join(missing) if missing
                               else "Required dependency files installed (versions are not certified automatically).") +
                              ("\nOptional not installed: " + ", ".join(optional) if optional else ""))
            self.wrapper.refresh_optional_mods()
        except (OSError, ValueError, KeyError) as ex:
            self.report_error("Cannot read installed mods: %s" % ex)

    def menu_for(self, mod):
        menu = QMenu(self)
        if len(mod.configs) == 1:
            menu.addAction("Open INI", lambda: self.owner.open_asset_path(str(mod.configs[0])))
        elif mod.configs:
            submenu = menu.addMenu("Open INI")
            for path in mod.configs:
                submenu.addAction(str(path.relative_to(self.wrapper.rt.dir)),
                                  lambda checked=False, p=path: self.owner.open_asset_path(str(p)))
        else:
            menu.addAction("No INI installed").setEnabled(False)
        return menu
