import configparser
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PySide6.QtWidgets import QApplication, QMessageBox

from cps import config
from cps.app import SettingsDialog
from cps.dependencies import (BUNDLED_RUNTIME, REQUIRED, OPTIONAL, bundled_runtime_files,
                              import_dependencies, missing_dependencies, require_dependencies)
from cps.runtime import Runtime
from cps.runtime_panels import ModsPanel
from cps.wrapper import WrapperTab


def installation(root, optional=False):
    for relatives in REQUIRED.values():
        for relative in relatives:
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"dependency fixture")
    (root / "data" / "bin" / "support.dll").write_bytes(b"azimuth sidecar")
    (root / "data" / "azimuth.ini").write_text(
        "[launcher]\nlook_in=E:\\DLC\n[title-1a96545b]\ncontent=E:\\DLC\\Island*test-placeholder\n")
    if optional:
        for relative in OPTIONAL.values():
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"optional fixture")
            path.with_suffix(".ini").write_text("[Mod]\nEnabled=1\n")
        (root / "FH2Mouse").mkdir()
        (root / "FH2Mouse" / "FH2Mouse.ini").write_text("[Keyboard]\nGas=W\n")


class DependencyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_import_only_known_dependencies_preserves_source_and_dlc(self):
        with tempfile.TemporaryDirectory() as temp:
            source, target = Path(temp) / "source", Path(temp) / "target"
            installation(source, True)
            private = ("data\\titles\\secret-save", "data\\logs\\azimuth.log", "data\\cache\\private",
                       "fh2-mods\\unknown.dll", "fh2-mods\\platform-session.json")
            for name in private:
                path = source / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"not imported")
            original = {str(path): path.read_bytes() for path in source.rglob("*") if path.is_file()}
            copied = import_dependencies(source, target, lambda message: None)
            self.assertTrue(copied)
            require_dependencies(target)
            self.assertTrue(all(not (target / name).exists() for name in private))
            self.assertTrue((target / "data" / "bin" / "support.dll").is_file())
            self.assertTrue((target / "FH2Mouse" / "FH2Mouse.ini").is_file())
            rt = Runtime(dict(config.DEFAULTS, sandbox_dir=str(target), mount="E:\\Games\\FH2\\Mount"))
            rt._write_launcher_ini()
            ini = configparser.ConfigParser(interpolation=None)
            ini.read(target / "data" / "azimuth.ini", encoding="utf-16")
            self.assertEqual(ini["title-1a96545b"]["content"], "E:\\DLC\\Island*test-placeholder")
            self.assertEqual(ini["launcher"]["look_in"], "E:\\DLC|E:\\Games")
            self.assertEqual(original, {str(path): path.read_bytes() for path in source.rglob("*") if path.is_file()})

    def test_preserves_destination_configs_and_updates_dependency_binary(self):
        with tempfile.TemporaryDirectory() as temp:
            source, target = Path(temp) / "source", Path(temp) / "target"
            installation(source, True)
            import_dependencies(source, target, lambda message: None)
            for relative in ("data\\azimuth.ini", "FH2_ExtraOptions.ini", "fh2-mods\\ShowFPS.ini"):
                (target / relative).write_bytes(b"destination configuration")
            (source / "azimuth.exe").write_bytes(b"updated binary")
            import_dependencies(source, target, lambda message: None)
            self.assertEqual((target / "azimuth.exe").read_bytes(), b"updated binary")
            for relative in ("data\\azimuth.ini", "FH2_ExtraOptions.ini", "fh2-mods\\ShowFPS.ini"):
                self.assertEqual((target / relative).read_bytes(), b"destination configuration")

    def test_missing_dependency_blocks_import_prepare_and_launch_without_side_effects(self):
        with tempfile.TemporaryDirectory() as temp:
            source, target = Path(temp) / "source", Path(temp) / "target"
            source.mkdir()
            with self.assertRaisesRegex(ValueError, "Missing required wrapper dependencies"):
                import_dependencies(source, target)
            self.assertFalse(target.exists())
            rt = Runtime(dict(config.DEFAULTS, sandbox_dir=str(target)))
            with patch("cps.runtime.subprocess.Popen") as popen:
                for operation in (rt.prepare, rt.launch):
                    with self.assertRaisesRegex(ValueError, "Import dependencies"):
                        operation()
                popen.assert_not_called()
            self.assertFalse(target.exists())

    def test_optional_dependencies_are_truly_optional_and_empty_required_file_is_missing(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            installation(root)
            self.assertEqual(missing_dependencies(root), [])
            (root / "DWrite.dll").write_bytes(b"")
            self.assertIn("FH2 Mod Platform", missing_dependencies(root)[0])

    def test_same_folder_is_safe_and_nested_source_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            installation(root)
            self.assertEqual(import_dependencies(root, root, lambda message: None), [])
            with self.assertRaisesRegex(ValueError, "non-nested"):
                import_dependencies(root, root / "child")

    def test_package_boundary_is_an_exact_allowlist(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            installation(root, True)
            for relative in BUNDLED_RUNTIME:
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"studio")
            entries = list(bundled_runtime_files(root))
            self.assertEqual({name for name, _, _ in entries}, {"runtime\\" + name for name in BUNDLED_RUNTIME})
            self.assertEqual(len(entries), 4)
            (root / BUNDLED_RUNTIME[0]).unlink()
            with self.assertRaises(FileNotFoundError):
                list(bundled_runtime_files(root))

    def test_prepare_ignores_legacy_bundled_third_party_files(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            bundled, target = root / "bundle", root / "runtime"
            installation(bundled, True)
            installation(target)
            for relative in BUNDLED_RUNTIME:
                path = bundled / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"studio")
            rt = Runtime(dict(config.DEFAULTS, sandbox_dir=str(target), ultimate_save=False))
            with patch("cps.runtime.config.runtime_src", return_value=str(bundled)), \
                    patch.object(rt, "_write_launcher_ini"), patch.object(rt, "_write_extra_options"), \
                    patch.object(rt, "_write_dashboard_ini"), patch.object(rt, "install_save") as save:
                rt.prepare()
                save.assert_called_once()
            self.assertFalse((target / OPTIONAL["ShowFPS"]).exists())
            self.assertEqual((target / "azimuth.exe").read_bytes(), b"dependency fixture")
            self.assertTrue((target / "fh2-mods" / "CarPackStudio.dll").exists())

    def test_prepared_runtime_does_not_depend_on_original_import_folder(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            installation(root)
            rt = Runtime(dict(config.DEFAULTS, sandbox_dir=str(root), ultimate_save=False,
                              dependency_source=str(root / "removed-installation")))
            with patch("cps.runtime.import_dependencies") as importer, \
                    patch.object(rt, "_write_launcher_ini"), patch.object(rt, "_write_extra_options"), \
                    patch.object(rt, "_write_dashboard_ini"), patch.object(rt, "install_save"):
                rt.prepare()
                importer.assert_not_called()

    def test_install_missing_dependencies_is_reported_in_gui(self):
        from cps.app import MainWindow
        owner = SimpleNamespace(build=lambda: "pack-build", _sync_extra_cars=lambda: None,
                                wrapper=SimpleNamespace(rt=SimpleNamespace(
                                    prepare=lambda log: require_dependencies("Z:\\Missing-Studio-Runtime"))),
                                log=lambda message: None)
        with patch("cps.app.QMessageBox.warning") as warning:
            self.assertFalse(MainWindow.install(owner))
            self.assertIn("Missing required wrapper dependencies", warning.call_args.args[2])

    def test_gui_optional_refresh_open_folder_and_import_are_runtime_scoped(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source, target = root / "source", root / "target"
            installation(source, True)
            wrapper = WrapperTab(dict(config.DEFAULTS, sandbox_dir=str(target)), None)
            wrapper.timer.stop()
            owner = SimpleNamespace(wrapper=wrapper, log=lambda message: None)
            panel = ModsPanel(owner)
            try:
                wrapper.refresh_optional_mods()
                self.assertFalse(wrapper.show_fps.isEnabled())
                with patch("cps.runtime_panels.os.startfile") as open_folder:
                    panel.open_button.click()
                    open_folder.assert_called_once_with(str(target / "fh2-mods"))
                with patch("cps.runtime_panels.QFileDialog.getExistingDirectory", return_value=str(source)), \
                        patch("cps.runtime_panels.QMessageBox.question", return_value=QMessageBox.Yes), \
                        patch("cps.runtime_panels.config.save") as save:
                    panel.import_button.click()
                    save.assert_called_once_with(wrapper.s)
                self.assertTrue(wrapper.show_fps.isEnabled())
                self.assertEqual(wrapper.s["dependency_source"], str(source))
                (target / OPTIONAL["ShowFPS"]).unlink()
                wrapper.refresh_optional_mods()
                self.assertFalse(wrapper.show_fps.isEnabled())
                panel.running_changed(True)
                self.assertFalse(panel.import_button.isEnabled())
            finally:
                panel.deleteLater()
                wrapper.shutdown()
                wrapper.overlay.deleteLater()
                wrapper.deleteLater()
                self.app.processEvents()

    def test_settings_keeps_explicit_dependency_source(self):
        settings = dict(config.DEFAULTS, dependency_source="E:\\Installed Azimuth")
        dialog = SettingsDialog(settings)
        try:
            self.assertEqual(dialog.result_settings()["dependency_source"], settings["dependency_source"])
            dialog.dependencies.setText("E:\\Other runtime")
            self.assertEqual(dialog.result_settings()["dependency_source"], "E:\\Other runtime")
        finally:
            dialog.deleteLater()
