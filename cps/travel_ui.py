"""Teleport menus and the persistent, map-qualified bookmark manager."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
                               QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView, QMessageBox)

from . import theme
from .travel import TRACKS, MAP_DESTINATIONS, load_places, track_label


def populate_places(menu, wrapper, track_id=None):
    menu.clear()
    try:
        entries = [(TRACKS[name], place, False) for name, destinations in MAP_DESTINATIONS.items()
                   for place in destinations]
        entries.extend((place.track_id, place.name, True) for place in load_places())
        for map_id, name, custom in entries:
            if track_id is not None and map_id != track_id:
                continue
            label = (("%s (%d) / " % (track_label(map_id), map_id)) if track_id is None else "") + name
            if custom:
                label += " [custom]"
            action = menu.addAction(label.replace("&", "&&"),
                                    lambda checked=False, t=map_id, p=name: wrapper.teleport_to(t, p))
            action.setEnabled(wrapper.rt.running() and map_id in TRACKS.values())
        if not menu.actions():
            menu.addAction("No teleport points").setEnabled(False)
    except (OSError, ValueError) as ex:
        message = "Cannot read custom teleports: %s" % ex
        wrapper.console.notice(message, theme.RED)
        menu.clear()
        menu.addAction(message).setEnabled(False)


class PlacesDialog(QDialog):
    def __init__(self, wrapper):
        super().__init__(wrapper.window())
        self.wrapper = wrapper
        self.setWindowTitle("My places")
        self.resize(760, 360)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["Map ID", "Map", "Name", "X", "Y", "Z"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSortingEnabled(True)
        self.table.sortItems(0, Qt.AscendingOrder)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.name = QLineEdit()
        self.name.setPlaceholderText("Name for the car's current position")
        self.add_button = QPushButton("Add current position")
        self.add_button.clicked.connect(self.add_current)
        self.delete_button = QPushButton("Delete selected")
        self.delete_button.clicked.connect(self.delete_selected)
        self.teleport_button = QPushButton("Teleport selected")
        self.teleport_button.clicked.connect(self.teleport_selected)
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self.refresh)
        close = QPushButton("Close")
        close.clicked.connect(self.close)
        self.note = QLabel()
        self.note.setWordWrap(True)
        layout = QVBoxLayout(self)
        layout.addWidget(self.table)
        add = QHBoxLayout()
        add.addWidget(self.name, 1)
        add.addWidget(self.add_button)
        layout.addLayout(add)
        buttons = QHBoxLayout()
        for button in (self.delete_button, self.teleport_button, refresh, close):
            buttons.addWidget(button)
        layout.addLayout(buttons)
        layout.addWidget(self.note)
        self.table.itemSelectionChanged.connect(self.update_buttons)
        wrapper.running_changed.connect(lambda running: self.update_buttons())
        wrapper.places_changed.connect(self.refresh)
        self.refresh()

    def selected(self):
        row = self.table.currentRow()
        item = self.table.item(row, 0) if row >= 0 else None
        return item.data(Qt.UserRole) if item else None

    def update_buttons(self):
        place = self.selected()
        self.add_button.setEnabled(self.wrapper.rt.running())
        self.delete_button.setEnabled(place is not None)
        self.teleport_button.setEnabled(place is not None and self.wrapper.rt.running() and
                                        place.track_id in TRACKS.values())

    def refresh(self):
        self.table.setRowCount(0)
        try:
            places = load_places()
            self.table.setSortingEnabled(False)
            for place in places:
                row = self.table.rowCount()
                self.table.insertRow(row)
                for column, value in enumerate((place.track_id, track_label(place.track_id), place.name, *place.position)):
                    item = QTableWidgetItem()
                    item.setData(Qt.DisplayRole, value)
                    if column == 0:
                        item.setData(Qt.UserRole, place)
                    self.table.setItem(row, column, item)
            self.table.setSortingEnabled(True)
            self.note.setText("%d saved places. Add captures the car's settled position; no coordinates are guessed." % len(places))
            self.note.setStyleSheet("")
        except (OSError, ValueError) as ex:
            self.error("Cannot read My places: %s" % ex)
        self.update_buttons()

    def error(self, message):
        self.note.setText(message)
        self.note.setStyleSheet("color:%s;" % theme.RED)
        self.wrapper.console.notice("error: " + message, theme.RED)

    def add_current(self):
        try:
            place = self.wrapper.add_current_place(self.name.text())
            self.name.clear()
            self.note.setText('Saved "%s" on %s.' % (place.name, track_label(place.track_id)))
            self.note.setStyleSheet("")
        except (OSError, ValueError) as ex:
            self.error(str(ex))

    def delete_selected(self):
        place = self.selected()
        if place is None:
            return
        if QMessageBox.question(self, "Delete My place", 'Delete "%s" on %s (%d)?' %
                                (place.name, track_label(place.track_id), place.track_id)) != QMessageBox.Yes:
            return
        try:
            self.wrapper.delete_place(place.track_id, place.name)
        except (OSError, ValueError) as ex:
            self.error(str(ex))

    def teleport_selected(self):
        place = self.selected()
        if place is not None:
            self.wrapper.teleport_to(place.track_id, place.name)
