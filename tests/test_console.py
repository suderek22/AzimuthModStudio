import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QComboBox, QLineEdit, QListWidget, QPushButton

from cps import config, theme
from cps.wrapper import Poller, WrapperTab


class ConsoleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        with patch.object(Poller, "start"):
            self.wrapper = WrapperTab(dict(config.DEFAULTS), None)
        self.wrapper.timer.stop()
        self.console = self.wrapper.console

    def tearDown(self):
        self.wrapper.shutdown()
        self.wrapper.overlay.deleteLater()
        self.wrapper.deleteLater()
        self.app.processEvents()

    def test_scrolling_preserves_position_and_latest_resumes(self):
        console = self.console
        console.resize(650, 200)
        console.show()
        for i in range(100):
            console.write("line %d" % i)
        self.app.processEvents()
        bar = console.out.verticalScrollBar()
        self.assertGreater(bar.maximum(), 0)
        bar.setValue(10)
        self.assertFalse(console.follow)
        self.assertFalse(console.latest.isHidden())
        console.inp.setFocus()
        console.write("new entry")
        self.app.processEvents()
        self.assertEqual(bar.value(), 10)
        console.follow_latest()
        console.write("following")
        self.assertEqual(bar.value(), bar.maximum())
        self.assertTrue(console.latest.isHidden())

    def test_wrapper_toolbar_spacing_with_theme_and_long_car_name(self):
        self.wrapper.setStyleSheet(theme.STYLE)
        self.wrapper.resize(1100, 700)
        self.wrapper.set_car("JAG_Etype_61", "1961 Jaguar E-type S1")
        self.wrapper.show()
        self.app.processEvents()
        controls = [self.wrapper.run_btn, self.wrapper.stop_btn, self.wrapper.detach_btn,
                    self.wrapper.boot, self.wrapper.show_overlay, self.wrapper.show_fps]
        for left, right in zip(controls, controls[1:]):
            self.assertGreaterEqual(right.geometry().left() - left.geometry().right(), 10)
        self.assertGreater(self.wrapper.car_lbl.geometry().top(),
                           self.wrapper.show_fps.geometry().bottom())

    def test_filters_reapply_history_exclude_and_clear(self):
        for text, resource in (("engine ready", "CarPackStudio"), ("engine failure", "Other"),
                               ("weather ready", "CarPackStudio"), ("engine spam", "CarPackStudio")):
            self.console.write(text, resource=resource)
        self.wrapper.send('filter text "engine"')
        self.wrapper.send('filter resource "CarPackStudio"')
        self.wrapper.send('filter -text "spam"')
        text = self.console.out.toPlainText()
        self.assertIn("engine ready", text)
        self.assertNotIn("weather ready", text)
        self.assertNotIn("engine failure", text)
        self.assertNotIn("engine spam", text)
        self.assertTrue(self.console.filter_btn.styleSheet())
        self.wrapper.send('filter -resource "CarPackStudio"')
        self.assertNotIn("engine ready", self.console.out.toPlainText())
        self.wrapper.send("filter remove")
        for text in ("engine ready", "engine failure", "weather ready", "engine spam"):
            self.assertIn(text, self.console.out.toPlainText())
        self.assertEqual(self.console.filters, [])
        self.assertEqual(self.console.filter_btn.styleSheet(), "")

    def test_multiple_includes_are_or_and_case_insensitive(self):
        self.console.set_filters([("text", "ENGINE", False), ("text", "weather", False),
                                  ("resource", "studio", False)])
        self.console.write("engine ready", resource="CarPackStudio")
        self.console.write("weather ready", resource="CarPackStudio")
        self.console.write("engine elsewhere", resource="Other")
        self.assertIn("engine ready", self.console.out.toPlainText())
        self.assertIn("weather ready", self.console.out.toPlainText())
        self.assertNotIn("elsewhere", self.console.out.toPlainText())

    def test_filter_dialog_add_remove_clear_and_indicator(self):
        failures = []

        def edit_dialog():
            dialog = self.app.activeModalWidget()
            try:
                combos = dialog.findChildren(QComboBox)
                value = dialog.findChild(QLineEdit)
                listing = dialog.findChild(QListWidget)
                buttons = {button.text(): button for button in dialog.findChildren(QPushButton)}
                combos[0].setCurrentText("resource")
                combos[1].setCurrentText("exclude")
                value.setText("Other")
                buttons["Add"].click()
                self.assertEqual(self.console.filters, [("resource", "Other", True)])
                self.assertTrue(self.console.filter_btn.styleSheet())
                listing.setCurrentRow(0)
                buttons["Remove selected"].click()
                self.assertEqual(self.console.filters, [])
                value.setText("CarPackStudio")
                buttons["Add"].click()
                buttons["Clear all"].click()
                self.assertEqual(self.console.filters, [])
                self.assertEqual(self.console.filter_btn.styleSheet(), "")
            except Exception as ex:
                failures.append(ex)
            finally:
                if dialog:
                    dialog.accept()

        QTimer.singleShot(0, edit_dialog)
        self.console.filter_btn.click()
        if failures:
            raise failures[0]

    def test_status_noise_hidden_but_errors_and_other_commands_visible(self):
        for line in ("21:17:44.933 [CarPackStudio] command: car status",
                     "21:17:45.933 [CarPackStudio] command: car status ",
                     "21:17:46.933 [CarPackStudio] command: car 2101",
                     "21:17:47.933 [CarPackStudio] ERROR: car status unavailable"):
            self.wrapper._log_line("platform", line)
        text = self.console.out.toPlainText()
        self.assertNotIn("command: car status", text)
        self.assertIn("command: car 2101", text)
        self.assertIn("ERROR: car status unavailable", text)

    def test_debug_streams_real_logs_and_reports_limits(self):
        with patch.object(self.wrapper.rt, "session", return_value={"mods": {"CarPackStudio": "1", "Other Mod": "1"}}):
            self.wrapper.send('debug "Other Mod"')
            self.wrapper._log_line("platform", "12:00 [Other Mod] thread=42 response=ok")
            self.wrapper._log_line("platform", "12:00 [CarPackStudio] ordinary entry")
            self.wrapper._log_line("platform", "12:00 [CarPackStudio] ERROR loading")
            text = self.console.out.toPlainText()
            self.assertIn("thread=42 response=ok", text)
            self.assertNotIn("ordinary entry", text)
            self.assertIn("ERROR loading", text)
            self.assertIn("no native thread/function tracing", text)
            self.wrapper.send("debug Azimuth")
            self.wrapper._log_line("azimuth", "tid=42 pipeline message")
            self.assertIn("tid=42 pipeline message", self.console.out.toPlainText())
            self.wrapper.send('debug "does not exist"')
            self.assertIn("Unknown log source", self.console.out.toPlainText())
            self.wrapper.send("debug off")
            self.assertEqual(self.wrapper.debug_targets, set())

    def test_detach_close_reattaches_same_console_and_keeps_state(self):
        console = self.console
        console.hist.append("car 333")
        console.inp.setText("draft")
        self.wrapper.send("console 1")
        self.app.processEvents()
        self.assertIsNotNone(self.wrapper.console_window)
        self.assertGreaterEqual(self.wrapper.console_window.width(), 1000)
        self.assertGreaterEqual(self.wrapper.console_window.height(), 700)
        self.wrapper.console_window.resize(160, 180)
        self.assertGreaterEqual(self.wrapper.console_window.width(), 640)
        self.assertGreaterEqual(self.wrapper.console_window.height(), 400)
        self.assertIs(console.parentWidget(), self.wrapper.console_window)
        console.write("detached output")
        self.wrapper.console_window.close()
        self.app.processEvents()
        self.assertIsNone(self.wrapper.console_window)
        self.assertIs(console.parentWidget(), self.wrapper.console_split)
        self.assertEqual(console.hist, ["car 333"])
        self.assertEqual(console.inp.text(), "draft")
        self.assertIn("detached output", console.out.toPlainText())
        self.wrapper.send("console 1")
        self.wrapper.send("console 0")
        self.assertIsNone(self.wrapper.console_window)

    def test_invalid_local_commands_never_reach_game(self):
        with patch.object(self.wrapper.rt, "command") as command:
            for line in ('filter -text', 'filter text "unterminated', 'console 2', 'debug'):
                self.wrapper.send(line)
            command.assert_not_called()
        self.assertIn("error:", self.console.out.toPlainText())

    def test_log_reader_waits_for_complete_lines(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "platform.log"
            path.write_bytes(b"12:00 [CarPackStudio] command: car stat")
            with patch.object(self.wrapper.rt, "log_files", return_value={"platform": str(path)}):
                self.wrapper._pump_logs()
                self.assertEqual(self.console.out.toPlainText(), "")
                with path.open("ab") as stream:
                    stream.write(b"us\n12:01 [Other] ready\n")
                self.wrapper._pump_logs()
                self.assertNotIn("command: car status", self.console.out.toPlainText())
                self.assertIn("[Other] ready", self.console.out.toPlainText())


if __name__ == "__main__":
    unittest.main()
