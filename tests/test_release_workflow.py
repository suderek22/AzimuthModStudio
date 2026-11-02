import json
import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QMessageBox, QTextBrowser

from cps import config
from cps.app import MainWindow
from cps.pack import Project, PackCar, uninstall_car, generate_sql
from cps.wrapper import Poller


class ReleaseWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _installed_fixture(self, temp, legacy=False):
        root = Path(temp)
        mount, mods = root / "Mount", root / "runtime" / "fh2-mods"
        base = mount / "media" / "stripped" / "gamedbRC.slt"
        base.parent.mkdir(parents=True)
        sql = {}
        for car_id, media in ((20000, "CUSTOM_A"), (20001, "CUSTOM_B")):
            sql[str(car_id)] = ("-- Car %d (%s) cloned from BASE\n" % (car_id, media) +
                               "INSERT INTO Data_Car VALUES (%d, '%s');\n" % (car_id, media) +
                               "INSERT INTO List_UpgradeEngine VALUES (%d, %d);\n" % (car_id * 1000, car_id))
        with closing(sqlite3.connect(base)) as db:
            with db:
                db.executescript("CREATE TABLE Data_Car(Id INTEGER PRIMARY KEY, MediaName TEXT);"
                                 "CREATE TABLE List_UpgradeEngine(Id INTEGER, Ordinal INTEGER);"
                                 "INSERT INTO Data_Car VALUES(333, 'BASE');")
        for surface in ("mediaoverride", "mediapc"):
            target = mount / surface / "stripped" / "gamedbRC.slt"
            target.parent.mkdir(parents=True)
            with closing(sqlite3.connect(base)) as source, closing(sqlite3.connect(target)) as destination:
                source.backup(destination)
                destination.executescript("".join(sql.values()))
                destination.commit()
        folder = mods / "CarPacks" / "Example"
        folder.mkdir(parents=True)
        metadata = {"name": "Example", "cars": [{"id": car_id, "media": media, "name": "Car %d" % car_id}
                                               for car_id, media in ((20000, "CUSTOM_A"), (20001, "CUSTOM_B"))],
                    "manifest_lines": {"game:\\media\\cars\\custom_a.zip": "<Zip A/>",
                                       "game:\\media\\cars\\custom_b.zip": "<Zip B/>"}}
        if not legacy:
            metadata["car_sql"] = sql
        (folder / "pack.json").write_text(json.dumps(metadata), encoding="utf-8")
        (folder / "pack.sql").write_text("".join(sql.values()), encoding="utf-8")
        (mount / "media" / "zipmanifest.xml").write_text("<ZipFiles>\n</ZipFiles>", encoding="utf-8")
        return mount, mods, folder, base

    def test_per_car_uninstall_preserves_other_and_base_rows(self):
        with tempfile.TemporaryDirectory() as temp:
            mount, mods, folder, base = self._installed_fixture(temp)
            before = base.read_bytes()
            uninstall_car("Example", 20000, str(mount), str(mods))
            meta = json.loads((folder / "pack.json").read_text())
            self.assertEqual([car["id"] for car in meta["cars"]], [20001])
            self.assertNotIn("CUSTOM_A", (folder / "pack.sql").read_text())
            self.assertIn("CUSTOM_B", (folder / "pack.sql").read_text())
            self.assertTrue((folder / "pack.sql.bak").exists())
            self.assertEqual(base.read_bytes(), before)
            for surface in ("mediaoverride", "mediapc"):
                with closing(sqlite3.connect(mount / surface / "stripped" / "gamedbRC.slt")) as db:
                    self.assertEqual(db.execute("SELECT Id FROM Data_Car ORDER BY Id").fetchall(),
                                     [(333,), (20001,)])
                    self.assertEqual(db.execute("SELECT Ordinal FROM List_UpgradeEngine").fetchall(), [(20001,)])
            manifest = (mount / "mediapc" / "zipmanifest.xml").read_text(encoding="utf-8-sig")
            self.assertNotIn("<Zip A/>", manifest)
            self.assertIn("<Zip B/>", manifest)
            with closing(sqlite3.connect(":memory:")) as db:
                db.executescript("CREATE TABLE Data_Car(Id INTEGER PRIMARY KEY, MediaName TEXT);"
                                 "CREATE TABLE List_UpgradeEngine(Id INTEGER, Ordinal INTEGER);")
                db.executescript((folder / "pack.sql").read_text())
                self.assertEqual(db.execute("SELECT Id FROM Data_Car").fetchall(), [(20001,)])

    def test_legacy_pack_per_car_uninstall(self):
        with tempfile.TemporaryDirectory() as temp:
            mount, mods, folder, _ = self._installed_fixture(temp, legacy=True)
            uninstall_car("Example", 20001, str(mount), str(mods))
            self.assertIn("CUSTOM_A", (folder / "pack.sql").read_text())
            self.assertNotIn("CUSTOM_B", (folder / "pack.sql").read_text())
            self.assertEqual(list(json.loads((folder / "pack.json").read_text())["car_sql"]), ["20000"])

    def test_uninstall_rolls_back_on_manifest_write_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            mount, mods, folder, _ = self._installed_fixture(temp)
            before_sql = (folder / "pack.sql").read_bytes()
            before_meta = (folder / "pack.json").read_bytes()
            with patch("cps.pack._rebuild_manifest", side_effect=OSError("disk write failed")):
                with self.assertRaisesRegex(OSError, "disk write failed"):
                    uninstall_car("Example", 20000, str(mount), str(mods))
            self.assertEqual((folder / "pack.sql").read_bytes(), before_sql)
            self.assertEqual((folder / "pack.json").read_bytes(), before_meta)
            for surface in ("mediaoverride", "mediapc"):
                with closing(sqlite3.connect(mount / surface / "stripped" / "gamedbRC.slt")) as db:
                    self.assertIsNotNone(db.execute("SELECT Id FROM Data_Car WHERE Id=20000").fetchone())
            self.assertFalse((folder / "pack.sql.tmp").exists())

    def test_generated_sql_exposes_independent_car_sections(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp, "base.slt")
            with closing(sqlite3.connect(path)) as db:
                db.executescript("CREATE TABLE Data_Car(Id INTEGER PRIMARY KEY, MakeID INTEGER, MediaName TEXT);"
                                 "INSERT INTO Data_Car VALUES(333,1,'BASE');")
                db.commit()
            sections = {}
            project = Project(cars=[PackCar("CUSTOM_A", "BASE", 20000), PackCar("CUSTOM_B", "BASE", 20001)])
            sql, _ = generate_sql(project, str(path), sections)
            self.assertEqual(set(sections), {"20000", "20001"})
            self.assertIn("CUSTOM_A", sql)
            with closing(sqlite3.connect(path)) as db:
                db.executescript(sections["20001"])
                self.assertEqual(db.execute("SELECT Id FROM Data_Car ORDER BY Id").fetchall(),
                                 [(333,), (20001,)])

    def test_duplicate_installed_car_ids_are_not_removed(self):
        with tempfile.TemporaryDirectory() as temp:
            mount, mods, folder, _ = self._installed_fixture(temp)
            other = mods / "CarPacks" / "Other"
            other.mkdir()
            (other / "pack.json").write_text(json.dumps({"name": "Other", "cars": [{"id": 20000}]}))
            before = (folder / "pack.sql").read_bytes()
            with self.assertRaisesRegex(ValueError, "duplicate ID"):
                uninstall_car("Example", 20000, str(mount), str(mods))
            self.assertEqual((folder / "pack.sql").read_bytes(), before)

    def test_base_car_and_malformed_legacy_rejected_without_writes(self):
        with tempfile.TemporaryDirectory() as temp:
            mount, mods, folder, base = self._installed_fixture(temp, legacy=True)
            for car_id in (333, 99999):
                with self.assertRaises(ValueError):
                    uninstall_car("Example", car_id, str(mount), str(mods))
            (folder / "pack.sql").write_text("unrecognized SQL", encoding="utf-8")
            before = (folder / "pack.json").read_bytes()
            with self.assertRaisesRegex(ValueError, "cannot be split safely"):
                uninstall_car("Example", 20000, str(mount), str(mods))
            self.assertEqual((folder / "pack.json").read_bytes(), before)
            with self.assertRaises(ValueError):
                uninstall_car("..", 20000, str(mount), str(mods))

    def _window(self, temp):
        settings = dict(config.DEFAULTS, mount=temp, sandbox_dir=os.path.join(temp, "runtime"))
        with patch("cps.app.config.load", return_value=settings), \
                patch.object(Poller, "start"), patch.object(MainWindow, "_startup"):
            window = MainWindow()
        window.wrapper.timer.stop()
        return window

    def _cleanup(self, window):
        window.wrapper.shutdown()
        window.wrapper.overlay.deleteLater()
        window.deleteLater()
        self.app.processEvents()

    def test_project_remove_is_confirmed_and_keeps_source_files(self):
        with tempfile.TemporaryDirectory() as temp:
            window = self._window(temp)
            try:
                car = PackCar("CUSTOM_A", "BASE", 20000)
                window.project = Project("Example", cars=[car], path=os.path.join(temp, "project.cpsproj"))
                override = Path(car.folder(temp), "sample.xml")
                override.parent.mkdir(parents=True)
                override.write_text("<a/>")
                window.refresh_pack_list()
                window.pack_list.setCurrentRow(0)
                with patch("cps.app.QMessageBox.question", return_value=QMessageBox.No):
                    window.remove_car()
                self.assertEqual(len(window.project.cars), 1)
                with patch("cps.app.QMessageBox.question", return_value=QMessageBox.Yes):
                    window.remove_car()
                self.assertEqual(window.project.cars, [])
                self.assertTrue(override.exists())
                self.assertEqual(Project.load(window.project.path).cars, [])
            finally:
                self._cleanup(window)

    def test_uninstall_ui_blocks_running_game(self):
        with tempfile.TemporaryDirectory() as temp:
            window = self._window(temp)
            try:
                window.project = Project(cars=[PackCar("CUSTOM_A", "BASE", 20000)])
                window.refresh_pack_list()
                window.pack_list.setCurrentRow(0)
                with patch.object(window.wrapper.rt, "running", return_value=True), \
                        patch("cps.app.uninstall_car") as remove, patch("cps.app.QMessageBox.warning") as warning:
                    window.uninstall_selected_car()
                    remove.assert_not_called()
                    self.assertIn("Stop the wrapper", warning.call_args[0][2])
            finally:
                self._cleanup(window)

    def test_how_to_menu_displays_bundled_guide(self):
        with tempfile.TemporaryDirectory() as temp:
            window = self._window(temp)
            failures = []
            try:
                def inspect():
                    dialog = self.app.activeModalWidget()
                    try:
                        browser = dialog.findChild(QTextBrowser)
                        text = browser.toPlainText()
                        self.assertIn("Experimental release", text)
                        self.assertIn("car recover", text)
                        self.assertIn("Uninstall car from game database", text)
                        self.assertIn("Technical details", text)
                        self.assertNotIn("planned", text.lower())
                    except Exception as ex:
                        failures.append(ex)
                    finally:
                        dialog.accept()
                help_menu = next(action.menu() for action in window.menuBar().actions() if action.text() == "&Help")
                action = next(action for action in help_menu.actions() if action.text() == "How to")
                QTimer.singleShot(0, inspect)
                action.trigger()
                if failures:
                    raise failures[0]
            finally:
                self._cleanup(window)


if __name__ == "__main__":
    unittest.main()
