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
        self.places_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.places_directory.cleanup)
        self.places_config = patch.object(config, "APPDATA", self.places_directory.name)
        self.places_config.start()
        self.addCleanup(self.places_config.stop)
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
            self.assertIn("Mod details depend on the mod's own logging", text)
            with patch.object(self.wrapper.rt, "running", return_value=True), \
                 patch.object(self.wrapper.rt, "command", return_value="Azimuth D3D12 pipeline tracing enabled") as command:
                self.wrapper.send("debug Azimuth")
                command.assert_called_with("CarPackStudio pipelines on")
            self.wrapper._log_line("azimuth", "tid=42 pipeline message")
            self.assertIn("tid=42 pipeline message", self.console.out.toPlainText())
            self.wrapper._log_line("platform", "12:00 [CarPackStudio] [AzimuthPipeline] thread=42 CreatePipelineState hr=0")
            self.assertIn("CreatePipelineState hr=0", self.console.out.toPlainText())
            self.wrapper.send('debug "does not exist"')
            self.assertIn("Unknown log source", self.console.out.toPlainText())
            self.wrapper.send("debug off")
            self.assertEqual(self.wrapper.debug_targets, set())

    def test_teleport_routes_coordinates_and_tracks_same_car_recovery(self):
        self.wrapper.active_car = "MAZ_MX5_16"
        with patch("cps.wrapper.resolve_destination", return_value=("Nicea", (-335.113, 4.87142, -5814.69))), \
             patch.object(self.wrapper.rt, "running", return_value=True), \
             patch.object(self.wrapper.rt, "car_id", return_value=2267), \
             patch.object(self.wrapper.rt, "command", return_value="tp queued: waiting for free roam") as command:
            self.wrapper.send("tp Anthem Nicea")
            command.assert_called_once_with("CarPackStudio teleport at 542 -335.113 4.87142 -5814.69")
        self.assertEqual(self.wrapper.swap_target, 2267)
        self.assertEqual(self.wrapper.swap_previous_id, 2267)
        self.assertIn("Nicea", self.wrapper.state.text())

    def test_invalid_teleport_and_unavailable_pipeline_debug_do_not_enable_actions(self):
        with patch.object(self.wrapper.rt, "command") as command:
            self.wrapper.send("tp Unknown festival")
            self.wrapper.send("tp Anthem")
            self.wrapper.send("debug Azimuth")
            command.assert_not_called()
        self.assertFalse(self.wrapper.debug_targets)
        self.assertIn("Start the wrapper", self.console.out.toPlainText())

    def test_teleport_list_is_available_offline_and_lists_every_entry(self):
        from cps.travel import DESTINATIONS
        with patch.object(self.wrapper.rt, "command") as command:
            self.wrapper.send("tp list")
            command.assert_not_called()
        text = self.console.out.toPlainText()
        for name in DESTINATIONS:
            self.assertIn("tp Anthem " + name, text)
        self.assertIn("tp Anthem Airport", text)
        self.assertIn("tp TheIsland Festival", text)

    def test_airport_command_sends_exact_coordinates(self):
        with patch.object(self.wrapper.rt, "running", return_value=True), \
             patch.object(self.wrapper.rt, "command", return_value="tp queued: waiting for free roam") as command:
            self.wrapper.send("tp Anthem Airport")
            command.assert_called_once_with("CarPackStudio teleport at 542 425.574 110.821 -1478.082")

    def test_island_teleport_is_map_bound(self):
        with patch.object(self.wrapper.rt, "running", return_value=True), \
             patch.object(self.wrapper.rt, "command", return_value="tp queued: waiting for free roam") as command:
            self.wrapper.send("tp TheIsland Outpost")
            command.assert_called_once_with("CarPackStudio teleport at 549 9218.77 139.458 -2889.03")

    def test_spawn_shortcuts_preserve_native_arguments(self):
        requests = ("spawn car 2267", "spawn ai 363", "spawn drivatar 333", "spawn list",
                    "spawn list 1", "spawn status", "spawn remove car 1", "spawn clear",
                    "spawn swap 1", "spawn swap reset", "spawn swap status")
        with patch.object(self.wrapper.rt, "command", return_value="spawn request accepted") as command:
            for request in requests:
                with self.subTest(request=request):
                    self.wrapper.send(request)
                    command.assert_called_with("CarPackStudio " + request)
        self.assertIsNone(self.wrapper.swap_target)

    def test_spawn_native_errors_and_help_are_visible(self):
        error = "error: spawn limit is 4 cars; remove an object or use spawn clear"
        with patch.object(self.wrapper.rt, "command", return_value=error) as command:
            self.wrapper.send("spawn car 363")
            self.assertIn(error, self.console.out.toPlainText())
            command.reset_mock()
            self.wrapper.on_command("help")
            command.assert_not_called()
        self.assertIn("spawn remove car <object ID>", self.console.out.toPlainText())
        self.assertIn("one car at a time", self.console.out.toPlainText())
        self.assertIn("four cars total", self.console.out.toPlainText())
        self.assertIn("AI starts paused", self.console.out.toPlainText())
        self.assertIn("drivatar pause|play|reset [object ID]", self.console.out.toPlainText())
        self.assertIn("spawn swap <object ID>|reset|status", self.console.out.toPlainText())
        self.assertIn("driverless cars only", self.console.out.toPlainText())

    def test_spawn_loading_interlock_error_is_visible(self):
        error = "error: wait for the previous car to finish loading/removing; spawn one car at a time"
        with patch.object(self.wrapper.rt, "command", return_value=error):
            self.wrapper.send("spawn ai 363")
        self.assertIn(error, self.console.out.toPlainText())

    def test_drivatar_shortcuts_preserve_scoped_arguments_and_normalize_name(self):
        requests = ("drivatar pause", "drivatar play 1", "drivatar reset", "drivatar pasue 1",
                    "Drivatar PaUsE 1", "drivatar 1 swap", "Drivatar swap reset")
        with patch.object(self.wrapper.rt, "command", return_value="drivatar request queued") as command:
            for request in requests:
                with self.subTest(request=request):
                    self.wrapper.send(request)
                    operation = "drivatar" + request[len("drivatar"):]
                    command.assert_called_with("CarPackStudio " + operation)
        self.assertIsNone(self.wrapper.swap_target)

    def test_drivatar_native_failures_are_visible(self):
        error = "error: no matching Studio-owned AI car; use spawn ai <carID> and spawn list"
        with patch.object(self.wrapper.rt, "command", return_value=error):
            self.wrapper.send("drivatar play 9999")
        self.assertIn(error, self.console.out.toPlainText())

    def test_driverless_swap_rejection_is_visible(self):
        error = "error: swap requires an active driverless spawn car object, not spawn ai"
        with patch.object(self.wrapper.rt, "command", return_value=error):
            self.wrapper.send("drivatar 2 swap")
        self.assertIn(error, self.console.out.toPlainText())
        self.assertIsNone(self.wrapper.swap_target)

    def test_event_list_info_start_stop_and_status(self):
        from cps.gamedata import EventRace, GameData
        races = [EventRace(i, "Race %d" % i, "Anthem", 1000 + i, 20 + i, 3) for i in range(1, 28)]
        races.append(EventRace(50, "Island race", "TheIsland", 1080, 88, 1))
        with patch.object(GameData, "event_races", return_value=races), \
             patch.object(self.wrapper.rt, "command", return_value="event start queued: race 2") as command, \
             patch.object(self.wrapper.rt, "running", return_value=True):
            self.wrapper.send("event list Anthem 2")
            text = self.console.out.toPlainText()
            self.assertIn("page 2/2 (27 total)", text)
            self.assertIn("27 | Anthem | Race 27", text)
            self.assertNotIn("Island race", text)
            command.assert_not_called()
            self.wrapper.send("event info 50")
            self.assertIn("50 | TheIsland | Island race", self.console.out.toPlainText())
            command.assert_not_called()
            self.wrapper.send("event START 2")
            command.assert_called_once_with("CarPackStudio event start 2")
            self.wrapper.send("event stop")
            self.assertEqual(command.call_args.args[0], "CarPackStudio event stop")
            self.wrapper.send("event status")
            self.assertEqual(command.call_args.args[0], "CarPackStudio event status")

    def test_invalid_event_input_or_offline_start_never_dispatches(self):
        from cps.gamedata import EventRace, GameData
        with patch.object(GameData, "event_races", return_value=[EventRace(1, "Race", "Anthem", 500, 2, 1)]), \
             patch.object(self.wrapper.rt, "command") as command:
            for value in ("event", "event start", "event start 549", "event start -1", "event start 1 extra",
                          "event list unknown", "event list Anthem 0", "event list Anthem 999",
                          "event start 9999999999999999999999", "event stop extra"):
                self.wrapper.send(value)
            with patch.object(self.wrapper.rt, "running", return_value=False):
                self.wrapper.send("event start 1")
            command.assert_not_called()
        self.assertIn("error:", self.console.out.toPlainText())

    def test_event_catalogue_errors_and_native_failure_are_visible(self):
        import sqlite3
        import zipfile
        from cps.gamedata import EventRace, GameData
        for error in (sqlite3.DatabaseError("bad event database"), zipfile.BadZipFile("bad string archive")):
            with self.subTest(error=type(error).__name__), \
                 patch.object(GameData, "event_races", side_effect=error), \
                 patch.object(self.wrapper.rt, "command") as command:
                self.wrapper.send("event list")
                command.assert_not_called()
                self.assertIn("error: %s" % error, self.console.out.toPlainText())
        with patch.object(GameData, "event_races", return_value=[EventRace(1, "Race", "Anthem", 500, 2, 1)]), \
             patch.object(self.wrapper.rt, "running", return_value=True), \
             patch.object(self.wrapper.rt, "command", return_value="error: load track 542 and wait for settled free roam"):
            self.wrapper.send("event start 1")
        self.assertIn("error: load track 542", self.console.out.toPlainText())
        self.assertIsNone(self.wrapper.swap_target)

    def test_custom_teleport_add_list_recall_remove_and_restart_persistence(self):
        from cps.travel import load_places
        position = "tp track=549 settled=1 queued=0 active=0 applied=0 position=123.25,45.5,-100"
        with patch.object(self.wrapper.rt, "running", return_value=True), \
             patch.object(self.wrapper.rt, "command", return_value=position) as command:
            self.wrapper.send("tp add My island test")
            command.assert_called_once_with("CarPackStudio teleport capture")
        self.assertEqual(load_places()[0].position, (123.25, 45.5, -100))
        self.wrapper.send("tp list")
        text = self.console.out.toPlainText()
        self.assertIn('tp TheIsland "My island test" [custom]', text)
        self.assertGreater(text.index('[custom]'), text.index('tp TheIsland Docks'))
        with patch.object(self.wrapper.rt, "running", return_value=True), \
             patch.object(self.wrapper.rt, "command", return_value="tp queued: waiting for free roam") as command:
            self.wrapper.send('tp TheIsland "My island test"')
            command.assert_called_once_with("CarPackStudio teleport at 549 123.25 45.5 -100")
        with patch.object(self.wrapper.rt, "running", return_value=True), \
             patch.object(self.wrapper.rt, "command",
                          side_effect=[position, "tp queued: waiting for free roam"]) as command:
            self.wrapper.send('tp "My island test"')
            self.assertEqual(command.call_args.args[0], "CarPackStudio teleport at 549 123.25 45.5 -100")
        self.wrapper.send("tp remove my ISLAND test")
        self.assertEqual(load_places(), [])

    def test_custom_place_validation_and_failed_capture_leave_file_unchanged(self):
        from cps.travel import load_places
        with patch.object(self.wrapper.rt, "command", return_value="error: wait for settled free roam"):
            self.wrapper.send("tp add Test")
        self.assertEqual(load_places(), [])
        position = "tp track=549 settled=1 queued=0 active=0 applied=0 position=1,2,3"
        with patch.object(self.wrapper.rt, "running", return_value=True), \
             patch.object(self.wrapper.rt, "command", return_value=position):
            self.wrapper.send("tp add Festival")
            self.wrapper.send("tp add Test")
            self.wrapper.send("tp add TEST")
        self.assertEqual(len(load_places()), 1)
        self.assertIn("built-in", self.console.out.toPlainText())
        self.assertIn("already exists", self.console.out.toPlainText())
        self.wrapper.send("tp remove Festival")
        self.assertEqual(len(load_places()), 1)
        with patch("cps.travel.os.replace", side_effect=OSError("write denied")), \
             patch.object(self.wrapper.rt, "running", return_value=True), \
             patch.object(self.wrapper.rt, "command", return_value=position):
            self.wrapper.send("tp add Other")
        self.assertEqual(len(load_places()), 1)
        self.assertIn("write denied", self.console.out.toPlainText())

    def test_custom_places_on_future_map_ids_do_not_enable_unverified_track_switch(self):
        from cps.travel import load_places
        position = "tp track=7777 settled=1 queued=0 active=0 applied=0 position=1,2,3"
        with patch.object(self.wrapper.rt, "running", return_value=True), \
             patch.object(self.wrapper.rt, "command", return_value=position):
            self.wrapper.send("tp add Custom Map Pit")
        self.assertEqual(load_places()[0].track_id, 7777)
        with patch.object(self.wrapper.rt, "running", return_value=True), \
             patch.object(self.wrapper.rt, "command", return_value="tp queued: waiting for free roam") as command:
            self.wrapper.send('tp 7777 "Custom Map Pit"')
            command.assert_called_once_with("CarPackStudio teleport at 7777 1 2 3")
        with patch.object(self.wrapper.rt, "command") as command:
            self.wrapper.send("track 7777")
            command.assert_not_called()

    def test_track_commands_resolve_names_aliases_and_ids_without_restart(self):
        for value, expected in (("TheIsland", 549), ("Storm Island", 549), ("549", 549),
                                ("Anthem", 542), ("mainland", 542), ("542", 542)):
            with self.subTest(value=value), patch.object(self.wrapper.rt, "running", return_value=True), \
                 patch.object(self.wrapper.rt, "command", return_value="track queued: %d" % expected) as command, \
                 patch.object(self.wrapper, "start") as start, patch.object(self.wrapper, "stop") as stop:
                self.wrapper.send("track " + value)
                command.assert_called_once_with("CarPackStudio track %d" % expected)
                start.assert_not_called()
                stop.assert_not_called()
                self.assertIsNone(self.wrapper.swap_target)

    def test_track_list_offline_invalid_commands_and_status(self):
        with patch.object(self.wrapper.rt, "command") as command:
            self.wrapper.send("track list")
            self.wrapper.send("track")
            self.wrapper.send("track 999")
            self.wrapper.send("track TheIsland")
            command.assert_not_called()
        text = self.console.out.toPlainText()
        self.assertIn("track Anthem (542)", text)
        self.assertIn("track TheIsland (549)", text)
        self.assertIn("Unknown track '999'", text)
        self.assertIn("Start the wrapper", text)
        with patch.object(self.wrapper.rt, "command", return_value="track current=549 settled=1") as command:
            self.wrapper.send("track status")
            command.assert_called_once_with("CarPackStudio track status")
        self.assertIn("current=549", self.console.out.toPlainText())

    def test_track_native_error_is_visible_and_does_not_start_car_recovery(self):
        with patch.object(self.wrapper.rt, "running", return_value=True), \
             patch.object(self.wrapper.rt, "command", return_value="error: map travel already in progress"):
            self.wrapper.send("track TheIsland")
        self.assertIn("error: map travel already in progress", self.console.out.toPlainText())
        self.assertIsNone(self.wrapper.swap_target)

    def test_scripts_window_is_reused_and_status_transport_is_hidden(self):
        self.wrapper.show_scripts()
        first = self.wrapper.scripts_window
        self.wrapper.show_scripts()
        self.assertIs(self.wrapper.scripts_window, first)
        self.wrapper._log_line("platform", "12:00 [CarPackStudio] command: input status")
        self.assertNotIn("command: input status", self.console.out.toPlainText())
        first.timer.stop()
        first.close()
        first.deleteLater()
        self.wrapper.scripts_window = None

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
