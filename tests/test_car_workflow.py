import io
import sqlite3
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from contextlib import closing

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from cps import config
from cps.app import CarBrowser, MainWindow
from cps.diagnose import _check_zip
from cps.pack import PackCar, Project, build_car_zip, import_car_archive
from cps.wrapper import Console, Poller, WrapperTab
from cps.runtime import Runtime


class CarWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_command_history_and_draft(self):
        console = Console()
        sent = []
        console.submit.connect(sent.append)
        QTest.keyClick(console.inp, Qt.Key_Up)
        self.assertEqual(console.inp.text(), "")
        for line in ("car 363", "car status", "car 333"):
            console.inp.setText(line)
            QTest.keyClick(console.inp, Qt.Key_Return)
        self.assertEqual(sent, console.hist)
        console.inp.setText("weather ")
        for expected in ("car 333", "car status", "car 363", "car 363"):
            QTest.keyClick(console.inp, Qt.Key_Up)
            self.assertEqual(console.inp.text(), expected)
        for expected in ("car status", "car 333", "weather ", "weather "):
            QTest.keyClick(console.inp, Qt.Key_Down)
            self.assertEqual(console.inp.text(), expected)
        console.deleteLater()

    def test_numeric_id_column_and_filter(self):
        browser = CarBrowser(None)
        browser.load([SimpleNamespace(title="Car A", media="AAA", source="base", id=100),
                      SimpleNamespace(title="Car B", media="BBB", source="mod", id=2)])
        self.assertEqual([browser.model.headerData(i, Qt.Horizontal) for i in range(4)],
                         ["ID", "Car", "Media", "Source"])
        browser.proxy.sort(0, Qt.AscendingOrder)
        self.assertEqual(browser.proxy.index(0, 0).data(), 2)
        browser.search.setText("100")
        self.assertEqual(browser.proxy.rowCount(), 1)
        self.assertEqual(browser.car_at(browser.proxy.index(0, 3)).media, "AAA")
        for column in range(4):
            self.assertEqual(browser.car_at(browser.proxy.index(0, column)).id, 100)
        browser.deleteLater()

    def test_test_in_game_swaps_without_restarting(self):
        wrapper = Mock()
        wrapper.rt.running.return_value = True
        wrapper.forced_car = None
        owner = SimpleNamespace(wrapper=wrapper)
        MainWindow._test_car(owner, "SUB_Impreza22b_98", "Subaru", 363)
        wrapper.send.assert_called_once_with("car 363")
        wrapper.start.assert_not_called()
        wrapper.restart.assert_not_called()
        self.assertIsNone(wrapper.forced_car)

    def test_test_in_game_starts_in_selected_car_when_stopped(self):
        wrapper = Mock()
        wrapper.rt.running.return_value = False
        owner = SimpleNamespace(wrapper=wrapper)
        MainWindow._test_car(owner, "FER_Enzo_02", "Enzo", 333)
        wrapper.start.assert_called_once_with()
        wrapper.send.assert_not_called()
        wrapper.restart.assert_not_called()
        self.assertEqual(wrapper.forced_car, 333)

    def test_running_swap_surfaces_native_error_without_restart(self):
        with patch.object(Poller, "start"):
            wrapper = WrapperTab(dict(config.DEFAULTS), None)
        wrapper.timer.stop()
        try:
            with patch.object(wrapper.rt, "running", return_value=True), \
                    patch.object(wrapper.rt, "command", return_value="error: car ID 999999 does not exist") as command, \
                    patch.object(wrapper, "restart") as restart, patch.object(wrapper, "start") as start:
                MainWindow._test_car(SimpleNamespace(wrapper=wrapper), "UNKNOWN", "Unknown", 999999)
                command.assert_called_once_with("CarPackStudio car 999999")
                self.assertIn("error: car ID 999999 does not exist", wrapper.console.out.toPlainText())
                restart.assert_not_called()
                start.assert_not_called()
                self.assertIsNone(wrapper.forced_car)
        finally:
            wrapper.shutdown()
            wrapper.overlay.deleteLater()
            wrapper.deleteLater()

    def test_timeout_offers_explicit_recovery_and_completion_cancels_it(self):
        with patch.object(Poller, "start"):
            wrapper = WrapperTab(dict(config.DEFAULTS), None)
        wrapper.timer.stop()
        try:
            wrapper.active_car = "MAZ_MX5_16"
            with patch.object(wrapper.rt, "car_id", return_value=2267), \
                    patch.object(wrapper.rt, "command", return_value="swapping to car 2101"), \
                    patch("cps.wrapper.time.monotonic", return_value=100):
                wrapper.send("car 2101")
            self.assertEqual(wrapper.poller.swap_target, 2101)
            self.assertEqual(wrapper.swap_previous_id, 2267)
            with patch("cps.wrapper.time.monotonic", return_value=159.9):
                wrapper._check_swap_timeout()
            self.assertTrue(wrapper.recover_btn.isHidden())
            with patch.object(wrapper, "restart") as restart, \
                    patch.object(wrapper.rt, "running", return_value=True), \
                    patch("cps.wrapper.time.monotonic", return_value=160):
                wrapper._check_swap_timeout()
                restart.assert_not_called()
                self.assertFalse(wrapper.recover_btn.isHidden())
                self.assertIn("60 seconds", wrapper.console.out.toPlainText())
                wrapper.recover_swap()
                restart.assert_called_once_with()
                self.assertEqual(wrapper.forced_car, 2267)
                self.assertEqual(wrapper.rt.last_car, 2267)
            wrapper.on_swap_status((333, "complete 333"))
            self.assertEqual(wrapper.swap_target, 2101)
            wrapper.on_swap_status((2101, "complete 2101"))
            self.assertIsNone(wrapper.swap_target)
            self.assertIsNone(wrapper.poller.swap_target)
            self.assertTrue(wrapper.recover_btn.isHidden())
        finally:
            wrapper.shutdown()
            wrapper.overlay.deleteLater()
            wrapper.deleteLater()

    def test_timeout_without_previous_id_does_not_offer_unsafe_recovery(self):
        with patch.object(Poller, "start"):
            wrapper = WrapperTab(dict(config.DEFAULTS), None)
        wrapper.timer.stop()
        try:
            with patch.object(wrapper.rt, "command", return_value="swapping to car 2101"), \
                    patch("cps.wrapper.time.monotonic", return_value=100):
                wrapper.send("car 2101")
            with patch("cps.wrapper.time.monotonic", return_value=161), \
                    patch.object(wrapper, "restart") as restart:
                wrapper._check_swap_timeout()
                self.assertTrue(wrapper.recover_btn.isHidden())
                wrapper.recover_swap()
                restart.assert_not_called()
            self.assertIn("Stop/Run", wrapper.console.out.toPlainText())
        finally:
            wrapper.shutdown()
            wrapper.overlay.deleteLater()
            wrapper.deleteLater()

    def test_immediate_recovery_commands_and_crash_recovery_compatibility(self):
        with patch.object(Poller, "start"):
            wrapper = WrapperTab(dict(config.DEFAULTS), None)
        wrapper.timer.stop()
        try:
            with patch.object(wrapper.rt, "running", return_value=True), \
                    patch.object(wrapper.rt, "command", return_value="crash recovery on") as command, \
                    patch.object(wrapper, "restart") as restart:
                for line, expected in (("car recover", 2267), ("car force", 2101),
                                       ("CarPackStudio car recover", 2267),
                                       ("  CarPackStudio car   force  ", 2101)):
                    wrapper.swap_target = 2101
                    wrapper.swap_previous_id = 2267
                    wrapper.swap_timed_out = False
                    wrapper.send(line)
                    self.assertEqual(wrapper.forced_car, expected)
                    self.assertEqual(wrapper.rt.last_car, expected)
                self.assertEqual(restart.call_count, 4)
                command.assert_not_called()
                wrapper.send("recover")
                command.assert_called_with("CarPackStudio recover")
                wrapper.send("CarPackStudio recover")
                command.assert_called_with("CarPackStudio recover")
                for value in (0, 1):
                    wrapper.send("recover %d" % value)
                    command.assert_called_with("CarPackStudio recover %d" % value)
                self.assertEqual(restart.call_count, 4)
        finally:
            wrapper.shutdown()
            wrapper.overlay.deleteLater()
            wrapper.deleteLater()

    def test_recovery_commands_require_active_swap_and_known_previous_car(self):
        with patch.object(Poller, "start"):
            wrapper = WrapperTab(dict(config.DEFAULTS), None)
        wrapper.timer.stop()
        try:
            with patch.object(wrapper.rt, "running", return_value=True), \
                    patch.object(wrapper.rt, "command") as command, \
                    patch.object(wrapper, "restart") as restart:
                wrapper.send("car recover")
                wrapper.send("car force")
                restart.assert_not_called()
                wrapper.swap_target = 2101
                wrapper.send("car recover")
                restart.assert_not_called()
                self.assertIn("previous car ID is unknown", wrapper.console.out.toPlainText())
                wrapper.send("car force")
                restart.assert_called_once_with()
                self.assertEqual(wrapper.forced_car, 2101)
                command.assert_not_called()
            with patch.object(wrapper.rt, "running", return_value=False), \
                    patch.object(wrapper, "restart") as restart:
                wrapper.send("car force")
                restart.assert_not_called()
        finally:
            wrapper.shutdown()
            wrapper.overlay.deleteLater()
            wrapper.deleteLater()

    def test_previous_car_id_uses_active_database(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for layer, cid in (("media", 333), ("mediaoverride", 20000)):
                path = root / layer / "stripped" / "gamedbRC.slt"
                path.parent.mkdir(parents=True)
                with closing(sqlite3.connect(path)) as db:
                    db.execute("CREATE TABLE Data_Car(Id INTEGER, MediaName TEXT)")
                    db.execute("INSERT INTO Data_Car VALUES(?, 'CustomCar')", (cid,))
                    db.commit()
            settings = dict(config.DEFAULTS, mount=str(root))
            runtime = Runtime(settings)
            self.assertEqual(runtime.car_id("customcar"), 20000)
            self.assertIsNone(runtime.car_id("missing"))

    def test_live_label_and_browsing_do_not_conflict(self):
        with patch.object(Poller, "start"):
            wrapper = WrapperTab(dict(config.DEFAULTS), None)
        wrapper.timer.stop()
        wrapper.car_title_cb = lambda media: {"AAA": "First car", "BBB": "Second car"}.get(media, media)
        with patch.object(wrapper.rt, "running", return_value=True):
            wrapper.on_telemetry({"valid": True, "car": "AAA"})
            self.assertEqual(wrapper.car_lbl.text(), "Car: First car (AAA)")
            wrapper.set_car("CCC", "Selected for editing")
            self.assertEqual(wrapper.car_lbl.text(), "Car: First car (AAA)")
            wrapper.on_telemetry({"valid": False})
            wrapper.on_telemetry({"valid": True, "car": "BBB"})
            self.assertEqual(wrapper.car_lbl.text(), "Car: Second car (BBB)")
            self.assertEqual(wrapper.state.text(), "Driving: BBB")
            wrapper.on_telemetry({"valid": True, "car": "Unknown"})
            self.assertEqual(wrapper.car_lbl.text(), "Car: Unknown (Unknown)")
        wrapper.shutdown()
        wrapper.overlay.deleteLater()
        wrapper.deleteLater()

    def test_title_lookup_for_base_project_and_installed_cars(self):
        owner = SimpleNamespace(
            project=SimpleNamespace(cars=[PackCar("MOD", "BASE", year=1998, name="Custom")]),
            game=SimpleNamespace(cars=[SimpleNamespace(media="BASE", title="Base car")]),
            _mods_dir=lambda: "unused")
        with patch("cps.app.installed_packs", return_value=[{"cars": [{"media": "OTHER", "name": "Installed"}]}]):
            self.assertEqual(MainWindow._car_title(owner, "mod"), "1998 Custom")
            self.assertEqual(MainWindow._car_title(owner, "base"), "Base car")
            self.assertEqual(MainWindow._car_title(owner, "other"), "Installed")
            self.assertEqual(MainWindow._car_title(owner, "unknown"), "unknown")

    def test_zip_import_round_trip_and_clone_behavior(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "ImportedCar.zip"
            original = {"ImportedCar.carbin": b"cars/ImportedCar/model",
                        "models/body.modelbin": b"ImportedCar",
                        "textures/paint.swatchbin": b"ImportedCar"}
            with zipfile.ZipFile(source, "w") as archive:
                for name, data in original.items():
                    archive.writestr(name, data)
            project = Project(path=str(root / "pack" / "project.cpsproj"))
            car = PackCar("ImportedCPS", "DifferentLengthDonor", car_id=20000)
            import_car_archive(project, car, str(source))
            project.save()
            source.unlink()
            loaded = Project.load(project.path)
            self.assertEqual(loaded.cars[0].source_zip, car.source_zip)
            folder = Path(car.folder(project.dir))
            folder.mkdir(parents=True)
            (folder / "extra.txt").write_text("extra ImportedCar")
            data, _, _, count = build_car_zip(loaded, loaded.cars[0], "missing-donor.zip")
            self.assertEqual(count, 4)
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                self.assertEqual(archive.read("ImportedCPS.carbin"), b"cars/ImportedCPS/model")
                self.assertEqual(archive.read("models/body.modelbin"), b"ImportedCPS")
                self.assertEqual(archive.read("extra.txt"), b"extra ImportedCPS")
            built = root / "built.zip"
            built.write_bytes(data)
            results = []
            _check_zip(loaded, loaded.cars[0], str(built), str(root), lambda *args: results.append(args), "[test]")
            self.assertEqual(results, [])
            donor = root / "BASE.zip"
            with zipfile.ZipFile(donor, "w") as archive:
                archive.writestr("BASE.carbin", b"BASE")
            clone = PackCar("COPY", "BASE")
            data, _, _, _ = build_car_zip(project, clone, str(donor))
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                self.assertEqual(archive.read("COPY.carbin"), b"COPY")

    def test_import_rejects_bad_archive_and_duplicates_without_mutation(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            project = Project(path=str(root / "project.cpsproj"))
            source = root / "Empty.zip"
            with zipfile.ZipFile(source, "w") as archive:
                archive.writestr("readme.txt", "Not a car")
            with self.assertRaisesRegex(ValueError, "car archive"):
                import_car_archive(project, PackCar("Empty", "BASE", car_id=20000), str(source))
            self.assertEqual(project.cars, [])
            self.assertFalse((root / "sources").exists())
            with self.assertRaisesRegex(ValueError, "characters"):
                import_car_archive(project, PackCar("../escape", "BASE"), str(source))
            project.cars.append(PackCar("EXIST", "BASE", car_id=20000))
            with self.assertRaisesRegex(ValueError, "already exists"):
                import_car_archive(project, PackCar("Empty", "BASE", car_id=20000), str(source))
            self.assertEqual(len(project.cars), 1)


if __name__ == "__main__":
    unittest.main()
