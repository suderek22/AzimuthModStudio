import json
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage
from PySide6.QtNetwork import QNetworkReply
from PySide6.QtWidgets import QApplication, QDockWidget, QPushButton

from cps import APP_VERSION, config
from cps.app import CarBrowser, MainWindow, SettingsDialog
from cps.gamedata import EventRace
from cps.runtime import Runtime
from cps.runtime_panels import MapsPanel, EventsPanel, SpawnedPanel, ModsPanel, mod_inventory
from cps.runtime_state import parse_track_state, parse_spawned
from cps.screenshots import capture_game
from cps.travel import TeleportPlace, save_places
from cps.updates import Version, newer_release, UpdateChecker
from cps.wrapper import Poller, WrapperTab


def track(current=542, settled=1, queued=-1, target=-1, stage="idle", timed_out=0):
    return ("track current=%d settled=%d queued=%d target=%d stage=%s elapsed_ms=123 timed_out=%d" %
            (current, settled, queued, target, stage, timed_out))


def spawned(*objects):
    return "Studio spawned cars: %d/4, page 1/1 (use object ID for removal):%s" % (
        len(objects), "".join("\n" + obj for obj in objects))


def car(object_id=1, car_id=363, ai="none", possessed=0, state=2, removing=0, located=1):
    return ("car %d carID=%d state=%d active=1 removing=%d located=%d position=1,2,3 ai=%s possessed=%d" %
            (object_id, car_id, state, removing, located, ai, possessed))


class RuntimeStateTests(unittest.TestCase):
    def test_map_status_with_spaces_and_completion(self):
        state = parse_track_state(track(549, stage="complete; gameplay resumed"))
        self.assertEqual(state.current, 549)
        self.assertTrue(state.idle)
        self.assertFalse(parse_track_state(track(542, queued=549)).idle)
        self.assertFalse(parse_track_state(track(549, settled=0)).idle)

    def test_invalid_native_status_is_not_an_empty_success(self):
        for reply in ("error: unavailable", "", "track current=542 settled=1"):
            with self.assertRaises(ValueError):
                parse_track_state(reply)
            with self.assertRaises(ValueError):
                parse_spawned(reply)
        with self.assertRaises(ValueError):
            parse_spawned(spawned(car(), car()))
        with self.assertRaises(ValueError):
            parse_spawned(spawned(car()).replace("cars: 1", "cars: 2"))
        with self.assertRaises(ValueError):
            parse_spawned(spawned(car()).replace("possessed=0", ""))

    def test_spawned_native_types_states_and_possession(self):
        objects = parse_spawned(spawned(car(), car(2, ai="paused"), car(3, possessed=1), car(4, removing=1)))
        self.assertEqual([obj.id for obj in objects], [1, 2, 3, 4])
        self.assertTrue(objects[0].can_swap)
        self.assertFalse(objects[1].can_swap)
        self.assertFalse(objects[2].can_swap)
        self.assertFalse(objects[3].can_swap)
        self.assertEqual(objects[2].label, "Player controlled")
        self.assertEqual(objects[3].label, "Removing")
        self.assertIn("Loading", parse_spawned(spawned(car(state=1, located=0)))[0].label)
        self.assertEqual(parse_spawned(spawned()), [])


class RuntimeGuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.config_patch = patch.object(config, "APPDATA", self.directory.name)
        self.config_patch.start()
        self.addCleanup(self.config_patch.stop)
        self.settings = dict(config.DEFAULTS, mount=self.directory.name, sandbox_dir=self.directory.name)
        with patch.object(Poller, "start"):
            self.wrapper = WrapperTab(self.settings, None)
        self.wrapper.timer.stop()
        self.owner = SimpleNamespace(wrapper=self.wrapper, s=self.settings, log=Mock(), open_asset_path=Mock())
        self.panels = []
        self.wrapper.rt.proc = SimpleNamespace(pid=1234, poll=lambda: None)
        self.command = patch.object(self.wrapper.rt, "command", return_value=track())
        self.request = self.command.start()
        self.addCleanup(self.command.stop)

    def tearDown(self):
        for panel in self.panels:
            panel.deleteLater()
        self.wrapper.rt.proc = None
        self.wrapper.shutdown()
        self.wrapper.overlay.deleteLater()
        self.wrapper.deleteLater()
        self.app.processEvents()

    def panel(self, cls):
        panel = cls(self.owner)
        self.panels.append(panel)
        return panel

    def snapshot(self, reply, status=None, pid=1234):
        self.wrapper.on_native_data({"pid": pid, "track": status or track(), "spawn": reply})

    def test_car_context_actions_gate_runtime_and_route_ids(self):
        self.owner.open_car = self.owner.add_clone = self.owner.test_base_car = Mock()
        self.owner.spawn_car = Mock()
        browser = self.panel(CarBrowser)
        selected = SimpleNamespace(id=363)
        menu = browser.menu_for(selected)
        actions = {action.text(): action for action in menu.actions()}
        actions["Spawn"].trigger()
        self.owner.spawn_car.assert_called_with(selected, "spawn car")
        actions["Spawn Drivatar"].trigger()
        self.owner.spawn_car.assert_called_with(selected, "spawn ai")
        self.wrapper.rt.proc = None
        actions = {action.text(): action for action in browser.menu_for(selected).actions()}
        self.assertFalse(actions["Spawn"].isEnabled())
        self.assertFalse(actions["Spawn Drivatar"].isEnabled())

    def test_map_menu_has_builtins_custom_points_and_no_speedtraps(self):
        save_places([TeleportPlace("My dock", 549, (1, 2, 3)), TeleportPlace("Main only", 542, (4, 5, 6))])
        panel = self.panel(MapsPanel)
        panel.refresh()
        self.assertEqual(panel.model.item(0).data(Qt.DisplayRole), 542)
        menu = panel.menu_for(549)
        teleports = menu.actions()[1].menu()
        names = [action.text() for action in teleports.actions()]
        self.assertEqual(names[:4], ["Festival", "Car Meet", "Docks", "Barn Find"])
        self.assertIn("My dock [custom]", names)
        self.assertNotIn("Main only [custom]", names)
        self.assertFalse(any("speed" in name.lower() for name in names))
        with patch.object(self.wrapper, "navigate") as navigate:
            teleports.actions()[-1].trigger()
            navigate.assert_called_once_with(549, 'tp 549 "My dock"')
        self.wrapper.rt.proc = None
        self.assertFalse(panel.menu_for(549).actions()[1].isEnabled())

    def test_custom_map_id_is_preserved_without_claiming_native_support(self):
        save_places([TeleportPlace("Future", 7777, (1, 2, 3))])
        panel = self.panel(MapsPanel)
        panel.refresh()
        self.assertEqual(panel.model.item(2).data(Qt.UserRole), 7777)
        self.assertFalse(panel.menu_for(7777).actions()[0].isEnabled())

    def test_cross_map_sequence_waits_for_confirmed_arrival_once(self):
        self.request.side_effect = [track(), "track queued: 549"]
        with patch.object(self.wrapper, "send") as send:
            self.wrapper.navigate(549, 'tp 549 "Docks"')
            self.assertIsNotNone(self.wrapper.pending_navigation)
            send.assert_not_called()
            self.snapshot(spawned(), track(542, settled=0, target=549, stage="loading"))
            self.snapshot(spawned(), track(549, stage="arriving", target=549))
            send.assert_not_called()
            self.snapshot(spawned(), track(549, stage="complete; gameplay resumed"))
            send.assert_called_once_with('tp 549 "Docks"')
            self.snapshot(spawned(), track(549, stage="complete; gameplay resumed"))
            send.assert_called_once()
            self.assertIsNone(self.wrapper.pending_navigation)

    def test_current_map_action_skips_transition(self):
        with patch.object(self.wrapper, "send") as send:
            self.wrapper.navigate(542, 'tp 542 "Airport"')
            send.assert_called_once_with('tp 542 "Airport"')
        self.request.assert_called_once_with("CarPackStudio track status")

    def test_map_failures_never_send_followup(self):
        for failure in (track(542, stage="failed; DLC check"), track(542, target=549, timed_out=1),
                        "invalid status", track(542, target=542)):
            with self.subTest(failure=failure), patch.object(self.wrapper, "send") as send:
                self.wrapper.pending_navigation = (549, 'tp 549 "Docks"', time.monotonic())
                self.snapshot(spawned(), failure)
                self.assertIsNone(self.wrapper.pending_navigation)
                send.assert_not_called()

    def test_native_rejection_and_wrong_process_snapshot_are_safe(self):
        self.request.side_effect = [track(), "error: use spawn clear first"]
        self.wrapper.navigate(549, 'tp 549 "Docks"')
        self.assertIsNone(self.wrapper.pending_navigation)
        self.assertIn("spawn clear", self.wrapper.console.out.toPlainText())
        self.wrapper.pending_navigation = (549, "event start 2", time.monotonic())
        self.snapshot(spawned(), track(549), pid=4321)
        self.assertIsNotNone(self.wrapper.pending_navigation)

    def test_navigation_timeout_stop_and_conflicting_console_command(self):
        self.wrapper.pending_navigation = (549, "event start 2", time.monotonic() - 96)
        with patch.object(self.wrapper.rt, "game_window", return_value=None):
            self.wrapper.tick()
        self.assertIsNone(self.wrapper.pending_navigation)
        self.wrapper.pending_navigation = (549, "event start 2", time.monotonic())
        self.wrapper.send('tp 542 "Airport"')
        self.assertIn("Wait for the pending", self.wrapper.console.out.toPlainText())
        self.request.assert_not_called()
        self.wrapper._reported_running = True
        self.wrapper.rt.proc = None
        self.wrapper._report_running()
        self.assertIsNone(self.wrapper.pending_navigation)

    def test_event_ids_grouped_by_map_and_start_uses_map_sequence(self):
        panel = self.panel(EventsPanel)
        races = [EventRace(10, "Main race", "Anthem", 123, 4, 2),
                 EventRace(20, "Island race", "TheIsland", 456, 5, 1)]
        with patch("cps.runtime_panels.GameData.valid", return_value=True), \
                patch("cps.runtime_panels.GameData.event_races", return_value=races):
            panel.refresh()
        self.assertEqual(panel.model.item(0).child(0).data(Qt.DisplayRole), 10)
        self.assertEqual(panel.model.item(1).child(0).data(Qt.DisplayRole), 20)
        with patch.object(self.wrapper, "navigate") as navigate:
            panel.menu_for(races[1]).actions()[0].trigger()
            navigate.assert_called_once_with(549, "event start 20")
        panel.search.setText("Island race")
        self.assertEqual(panel.proxy.rowCount(), 1)

    def test_spawned_refresh_tracks_console_changes_selection_and_deletion(self):
        panel = self.panel(SpawnedPanel)
        self.snapshot(spawned(car(1), car(2, ai="paused")))
        self.assertEqual(panel.model.rowCount(), 2)
        item = panel.model.item(1)
        panel.tree.setCurrentIndex(panel.proxy.mapFromSource(item.index()))
        self.snapshot(spawned(car(2, ai="playing")))
        self.assertEqual(panel.model.rowCount(), 1)
        self.assertEqual(panel.at(panel.tree.currentIndex()).id, 2)
        self.assertEqual(panel.model.item(0, 3).text(), "playing")
        self.snapshot(spawned())
        self.assertEqual(panel.model.rowCount(), 0)
        self.snapshot(spawned(car()))
        panel.running_changed(False)
        self.assertEqual(panel.model.rowCount(), 0)

    def test_spawned_context_delete_swap_ai_controls_and_failure_gating(self):
        panel = self.panel(SpawnedPanel)
        self.snapshot(spawned(car()))
        obj = panel.model.item(0).data(Qt.UserRole)
        actions = {a.text(): a for a in panel.menu_for(obj).actions()}
        actions["Delete"].trigger()
        self.request.assert_called_with("CarPackStudio spawn remove car 1")
        actions["Swap to this car"].trigger()
        self.request.assert_called_with("CarPackStudio spawn swap 1")
        self.assertFalse(actions["AI behaviour"].isEnabled())
        self.snapshot(spawned(car(2, ai="paused")))
        obj = panel.model.item(0).data(Qt.UserRole)
        actions = {a.text(): a for a in panel.menu_for(obj).actions()}
        self.assertFalse(actions["Swap to this car"].isEnabled())
        ai = actions["AI behaviour"].menu()
        for action, command in zip(ai.actions()[:3], ("pause", "play", "reset")):
            action.trigger()
            self.request.assert_called_with("CarPackStudio drivatar %s 2" % command)
        self.assertTrue(all(not action.isEnabled() for action in ai.actions()[3:]))
        self.snapshot("error: disconnected")
        self.assertFalse(panel.available)
        self.assertFalse(panel.menu_for(obj).actions()[0].isEnabled())
        self.assertIn("stale", panel.note.text())

    def test_screenshot_default_overlay_and_toolbar_position(self):
        self.assertFalse(self.wrapper.show_overlay.isChecked())
        self.wrapper.resize(1100, 700)
        self.wrapper.show()
        self.app.processEvents()
        self.assertGreater(self.wrapper.screenshot_btn.x(), self.wrapper.show_fps.x())
        self.assertLess(self.wrapper.screenshot_btn.y(), self.wrapper.state.y())
        self.assertFalse(self.wrapper.screenshot_btn.icon().isNull())
        with patch("cps.screenshots.capture_game", return_value=(Path("test.png"), 1920, 1080)) as capture:
            self.wrapper.screenshot_btn.click()
            # Enable state is refreshed by the normal wrapper timer.
            self.wrapper._report_running()
            self.wrapper.screenshot_btn.click()
            capture.assert_called_once_with(self.wrapper.rt)
        self.assertIn("1920 x 1080", self.wrapper.console.out.toPlainText())

    def test_main_window_has_all_tabs_and_permanent_version(self):
        with patch("cps.app.config.load", return_value=self.settings), patch.object(Poller, "start"), \
                patch.object(MainWindow, "_startup"):
            window = MainWindow()
        window.wrapper.timer.stop()
        try:
            names = {dock.windowTitle() for dock in window.findChildren(QDockWidget)}
            self.assertTrue({"Explorer", "Cars", "Maps", "Events", "Spawned", "Mods"}.issubset(names))
            self.assertIn(APP_VERSION, window.version_label.text())
        finally:
            window.update_checker.shutdown()
            window.wrapper.shutdown()
            window.wrapper.overlay.deleteLater()
            window.deleteLater()


class ModAndCaptureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_mod_inventory_versions_aliases_configs_and_extraoptions(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            mods = root / "fh2-mods"
            mods.mkdir()
            for name in ("CarPackStudio.dll", "CarPackStudio.ini", "FH2NoPause.dll", "FH2Mouse.dll", "Unknown.dll"):
                (mods / name).touch()
            (root / "FH2Mouse").mkdir()
            (root / "FH2Mouse" / "FH2Mouse.ini").write_text("[input]")
            (root / "DWrite.dll").touch()
            (root / "winmm.dll").touch()
            (root / "FH2_ExtraOptions.ini").write_text("; Version v0.3.2\n[Global]")
            session = {"pid": 42, "mods": {"CarPackStudio": "1.0.0", "NoPause": "1.1.0",
                                          "FH2 Mouse & Keyboard": "1.1.0"}, "platform": "2.8.0"}
            rt = Runtime(dict(config.DEFAULTS, sandbox_dir=temp))
            with patch.object(rt, "session", return_value=session):
                rows = {mod.name: mod for mod in mod_inventory(rt)}
            self.assertEqual(rows["CarPackStudio"].version, "1.0.0")
            self.assertEqual(rows["FH2 Mod Platform"].version, "2.8.0")
            self.assertEqual(rows["FH2 Extra Options"].version, "0.3.2")
            self.assertEqual(rows["NoPause"].version, "1.1.0")
            self.assertEqual(rows["Unknown"].version, "Unknown (not reported)")
            self.assertEqual(rows["FH2 Mouse & Keyboard"].configs[0].name, "FH2Mouse.ini")
            wrapper = SimpleNamespace(rt=rt, running_changed=Mock(), native_snapshot=Mock())
            owner = SimpleNamespace(wrapper=wrapper, open_asset_path=Mock())
            panel = ModsPanel(owner)
            try:
                panel.menu_for(rows["CarPackStudio"]).actions()[0].trigger()
                owner.open_asset_path.assert_called_once_with(str(mods / "CarPackStudio.ini"))
                self.assertFalse(panel.menu_for(rows["Unknown"]).actions()[0].isEnabled())
            finally:
                panel.deleteLater()

    def test_screenshot_retains_exact_pixels_unique_names_and_appdata(self):
        image = QImage(800, 450, QImage.Format_RGB32)
        image.fill(Qt.blue)
        runtime = SimpleNamespace(running=lambda: True, game_window=lambda: 99)
        with tempfile.TemporaryDirectory() as temp, patch.object(config, "APPDATA", temp), \
                patch("cps.screenshots.capture_client", return_value=image) as capture:
            first, width, height = capture_game(runtime)
            second, _, _ = capture_game(runtime)
            self.assertNotEqual(first, second)
            self.assertEqual(first.parent, Path(temp) / "screenshots")
            self.assertEqual((width, height), (800, 450))
            saved = QImage(str(first))
            self.assertEqual((saved.width(), saved.height()), (800, 450))
            self.assertEqual(saved.pixelColor(10, 10), image.pixelColor(10, 10))
            capture.assert_called_with(99)

    def test_stopped_screenshot_and_stale_pipe_session_are_rejected(self):
        runtime = Runtime(dict(config.DEFAULTS))
        with self.assertRaisesRegex(ValueError, "Start the wrapper"):
            capture_game(runtime)
        runtime.proc = SimpleNamespace(pid=42, poll=lambda: None)
        with patch.object(runtime, "session", return_value={"pid": 99, "pipe": "wrong"}), patch("builtins.open") as opened:
            self.assertIn("session is not ready", runtime.command("CarPackStudio status"))
            opened.assert_not_called()

    def test_mod_inventory_invalid_session_reports_failure(self):
        runtime = Runtime(dict(config.DEFAULTS))
        with patch.object(runtime, "session", return_value=["invalid"]):
            with self.assertRaisesRegex(ValueError, "Invalid platform-session"):
                mod_inventory(runtime)

    def test_original_icon_has_vector_source_and_all_packaged_sizes(self):
        from PIL import Image
        from PySide6.QtGui import QIcon
        resources = Path(config.ROOT) / "resources"
        source = (resources / "app-icon.svg").read_text(encoding="utf-8")
        self.assertIn("<svg", source)
        self.assertNotIn("<image", source)
        with Image.open(resources / "app.ico") as icon:
            self.assertTrue({(16, 16), (32, 32), (48, 48), (256, 256)}.issubset(icon.ico.sizes()))
        self.assertFalse(QIcon(str(resources / "app.ico")).isNull())


class UpdateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_semantic_version_comparison_not_lexical(self):
        self.assertGreater(Version("v0.10.0"), Version("0.9.9"))
        self.assertGreater(Version("1.0.0"), Version("1.0.0-rc.2"))
        self.assertGreater(Version("1.0.0-rc.10"), Version("1.0.0-rc.2"))
        self.assertEqual(Version("1.0.0+build"), Version("1.0.0"))
        self.assertEqual(newer_release({"tag_name": "v0.2.0"}, "0.1.0"), "v0.2.0")
        self.assertIsNone(newer_release({"tag_name": "0.1.0"}, "0.1.0"))
        self.assertIsNone(newer_release({"tag_name": "0.0.9"}, "0.1.0"))
        for data in ({}, {"tag_name": "latest"}, {"tag_name": "0.2.0", "draft": True}):
            with self.assertRaises(ValueError):
                newer_release(data)

    def test_update_setting_opt_in_and_explicit_button(self):
        self.assertFalse(config.DEFAULTS["check_updates"])
        dialog = SettingsDialog(dict(config.DEFAULTS))
        try:
            dialog.updates.setChecked(True)
            self.assertTrue(dialog.result_settings()["check_updates"])
            self.assertTrue(any("Check updates now" == button.text() for button in dialog.findChildren(QPushButton)))
        finally:
            dialog.deleteLater()

    def test_async_check_timeout_single_request_and_shutdown(self):
        checker = UpdateChecker(None)
        reply = Mock()
        with patch.object(checker.manager, "get", return_value=reply) as get:
            checker.check(manual=False)
            checker.check(manual=False)
            get.assert_called_once()
            request = get.call_args.args[0]
            self.assertEqual(request.transferTimeout(), 15000)
            self.assertEqual(request.url().host(), "api.github.com")
            checker.shutdown()
            reply.abort.assert_called_once()
            self.assertIsNone(checker.reply)

    def test_network_failure_and_no_release_are_reported(self):
        checker = UpdateChecker(None)
        messages = []
        checker.message.connect(messages.append)
        for code, error, expected in ((404, 203, "No published"), (500, 401, "Cannot check")):
            reply = Mock()
            reply.attribute.return_value = code
            reply.error.return_value = error
            reply.errorString.return_value = "Service unavailable"
            checker.reply = reply
            checker.manual = False
            checker._finished()
            self.assertIn(expected, messages[-1])
            reply.deleteLater.assert_called_once()

    def test_no_new_version_is_silent_for_automatic_checks(self):
        checker = UpdateChecker(None)
        reply = Mock()
        reply.attribute.return_value = 200
        reply.error.return_value = QNetworkReply.NoError
        reply.readAll.return_value = json.dumps({"tag_name": APP_VERSION}).encode()
        checker.reply = reply
        checker.manual = False
        with patch("cps.updates.QMessageBox.information") as info:
            checker._finished()
            info.assert_not_called()

    def test_startup_network_contact_is_opt_in(self):
        for enabled in (False, True):
            owner = SimpleNamespace(s={"mount": "fixture", "check_updates": enabled}, load_game=Mock(),
                                    update_checker=Mock())
            with patch("cps.app.GameData.valid", return_value=True):
                MainWindow._startup(owner)
            if enabled:
                owner.update_checker.check.assert_called_once_with(manual=False)
            else:
                owner.update_checker.check.assert_not_called()

    def test_new_release_popup_contains_repo_link(self):
        from cps.updates import REPOSITORY
        checker = UpdateChecker(None)
        reply = Mock()
        reply.attribute.return_value = 200
        reply.error.return_value = QNetworkReply.NoError
        reply.readAll.return_value = b'{"tag_name":"v99.0.0"}'
        checker.reply = reply
        checker.manual = False
        shown = []

        def display(box):
            shown.append(box.text())
            return 0

        with patch("cps.updates.QMessageBox.exec", display):
            checker._finished()
        self.assertEqual(len(shown), 1)
        self.assertIn(REPOSITORY, shown[0])
        self.assertIn("v99.0.0", shown[0])
