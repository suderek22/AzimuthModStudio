import configparser
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import PropertyMock, patch

from PySide6.QtWidgets import QApplication, QPushButton

from cps import config
from cps.app import SettingsDialog
from cps.runtime import Runtime


class LaunchSettingsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        dependency_check = patch("cps.runtime.require_dependencies")
        dependency_check.start()
        self.addCleanup(dependency_check.stop)

    def test_settings_arguments_persist_and_reset(self):
        settings = dict(config.DEFAULTS)
        settings.pop("launch_arguments")
        dialog = SettingsDialog(settings)
        try:
            self.assertEqual(dialog.launch_args.text(), "--game=fh2 --skip-launcher")
            custom = '--game="E:\\Game folder\\" --replay="E:\\Scripts\\test run.json"'
            dialog.launch_args.setText(custom)
            with tempfile.TemporaryDirectory() as temp:
                with patch.object(config, "APPDATA", temp), patch.object(config, "SETTINGS", str(Path(temp) / "settings.json")):
                    config.save(dialog.result_settings())
                    self.assertEqual(config.load()["launch_arguments"], custom)
                    reopened = SettingsDialog(config.load())
                    self.assertEqual(reopened.launch_args.text(), custom)
                    reopened.deleteLater()
            dialog.launch_args.clear()
            self.assertEqual(dialog.result_settings()["launch_arguments"], "")
            reset = next(button for button in dialog.findChildren(QPushButton) if button.text() == "Reset to default")
            reset.click()
            self.assertEqual(dialog.launch_args.text(), config.DEFAULTS["launch_arguments"])
        finally:
            dialog.deleteLater()

    def test_launch_uses_windows_command_line_verbatim_without_shell(self):
        for mode in ("sandbox", "existing"):
            for arguments in (None, "", '  --game=fh2 --skip-launcher  ',
                              '--game="E:\\My Game" --replay="E:\\Scripts\\a b.json" & literal'):
                with self.subTest(mode=mode, arguments=arguments):
                    settings = dict(config.DEFAULTS, runtime_mode=mode, sandbox_dir="E:\\Test Runtime",
                                    azimuth_dir="E:\\Existing Runtime")
                    if arguments is None:
                        settings.pop("launch_arguments")
                    else:
                        settings["launch_arguments"] = arguments
                    rt = Runtime(settings)
                    expected_args = config.DEFAULTS["launch_arguments"] if arguments is None else arguments.strip()
                    expected = subprocess.list2cmdline([rt.exe]) + (" " + expected_args if expected_args else "")
                    with patch("cps.runtime.subprocess.Popen") as popen, \
                         patch.object(rt, "_restore_swap_flow"), patch.object(rt, "_backup_swap_flow"):
                        rt.launch()
                    popen.assert_called_once_with(expected, cwd=rt.dir, shell=False)

    def test_invalid_arguments_do_not_start_or_mutate_flow(self):
        for invalid in (None, [], "bad\0argument"):
            rt = Runtime(dict(config.DEFAULTS, launch_arguments=invalid))
            with patch("cps.runtime.subprocess.Popen") as popen, patch.object(rt, "_restore_swap_flow") as restore:
                with self.assertRaisesRegex(ValueError, "launch arguments"):
                    rt.launch()
                popen.assert_not_called()
                restore.assert_not_called()

    def test_real_windows_child_receives_quoted_paths_and_empty_values(self):
        values = ["E:\\My Game\\Mount", "E:\\Folder with spaces\\", "", "& literal"]
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "arguments.json"
            code = "import json,sys;from pathlib import Path;Path(sys.argv[1]).write_text(json.dumps(sys.argv[2:]))"
            arguments = subprocess.list2cmdline(["-c", code, str(output), *values])
            rt = Runtime(dict(config.DEFAULTS, runtime_mode="existing", azimuth_dir=temp,
                              launch_arguments=arguments))
            with patch.object(Runtime, "exe", new_callable=PropertyMock, return_value=sys.executable):
                rt.launch()
                try:
                    self.assertEqual(rt.proc.wait(timeout=10), 0)
                    self.assertEqual(json.loads(output.read_text()), values)
                finally:
                    rt.stop()

    def test_failed_launch_restores_flow_and_running_game_is_not_restarted(self):
        rt = Runtime(dict(config.DEFAULTS))
        with patch("cps.runtime.subprocess.Popen", side_effect=OSError("launch failed")), \
             patch.object(rt, "_restore_swap_flow") as restore, patch.object(rt, "_backup_swap_flow"):
            with self.assertRaises(OSError):
                rt.launch()
            self.assertEqual(restore.call_count, 2)
        with patch.object(rt, "running", return_value=True), patch("cps.runtime.subprocess.Popen") as popen:
            rt.launch()
            popen.assert_not_called()

    def test_launcher_preserves_dlc_content_and_discovery_paths(self):
        for encoding in ("utf-8", "utf-16"):
            with self.subTest(encoding=encoding), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                data = root / "data"
                data.mkdir()
                ini = data / "azimuth.ini"
                content = "E:\\DLC\\StormIsland*content-key"
                ini.write_text("[launcher]\nlook_in=E:\\DLC\n[title-1a96545b]\ncontent=" + content + "\n",
                               encoding=encoding)
                rt = Runtime(dict(config.DEFAULTS, sandbox_dir=temp, mount="E:\\Games\\FH2\\Mount"))
                for _ in range(2):
                    rt._write_launcher_ini()
                saved = configparser.ConfigParser(interpolation=None)
                saved.read(ini, encoding="utf-16")
                self.assertEqual(saved["title-1a96545b"]["content"], content)
                self.assertEqual(saved["launcher"]["look_in"], "E:\\DLC|E:\\Games")
