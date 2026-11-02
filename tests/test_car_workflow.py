import io
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from cps import config
from cps.app import CarBrowser, MainWindow
from cps.diagnose import _check_zip
from cps.pack import PackCar, Project, build_car_zip, import_car_archive
from cps.wrapper import Console, Poller, WrapperTab


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
        self.assertEqual(browser.model.headerData(3, Qt.Horizontal), "ID")
        browser.proxy.sort(3, Qt.AscendingOrder)
        self.assertEqual(browser.proxy.index(0, 3).data(), 2)
        browser.search.setText("100")
        self.assertEqual(browser.proxy.rowCount(), 1)
        self.assertEqual(browser.car_at(browser.proxy.index(0, 3)).media, "AAA")
        browser.deleteLater()

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
