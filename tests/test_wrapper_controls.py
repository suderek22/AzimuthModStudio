import ctypes
import tempfile
import threading
import time
import unittest
import uuid
from contextlib import contextmanager
from ctypes import wintypes
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QMenu, QMessageBox

from cps import config
from cps import command_pipe
from cps.app import MainWindow
from cps.runtime import Runtime, focus_game_window
from cps.runtime_panels import EventsPanel, MapsPanel, SpawnedPanel
from cps.travel import TeleportPlace, load_places, save_places
from cps.travel_ui import PlacesDialog, populate_places
from cps.volume import validate_volume
from cps.wrapper import GameContainer, Poller, WrapperTab


@contextmanager
def pipe_server(reply=b"ready", pause=0):
    """A real message-mode Windows server, independent of any game process."""
    kernel = command_pipe.kernel32
    kernel.CreateNamedPipeW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
                                        wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p]
    kernel.CreateNamedPipeW.restype = wintypes.HANDLE
    kernel.ConnectNamedPipe.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
    kernel.ConnectNamedPipe.restype = wintypes.BOOL
    name = "\\\\.\\pipe\\fh2mod-test-" + uuid.uuid4().hex
    handle = kernel.CreateNamedPipeW(name, 3, 6, 1, 262144, 65536, 0, None)
    if handle == command_pipe.INVALID_HANDLE_VALUE:
        raise ctypes.WinError(ctypes.get_last_error())
    requests = []
    errors = []

    def serve():
        try:
            if not kernel.ConnectNamedPipe(handle, None) and ctypes.get_last_error() != 535:
                raise ctypes.WinError(ctypes.get_last_error())
            buffer = ctypes.create_string_buffer(65536)
            count = wintypes.DWORD()
            if not kernel.ReadFile(handle, buffer, len(buffer), ctypes.byref(count), None):
                raise ctypes.WinError(ctypes.get_last_error())
            requests.append(buffer.raw[:count.value])
            time.sleep(pause)
            kernel.WriteFile(handle, reply, len(reply), ctypes.byref(count), None)
        except OSError as ex:
            errors.append(ex)
        finally:
            kernel.CloseHandle(handle)

    server = threading.Thread(target=serve)
    server.start()
    try:
        yield name, requests
    finally:
        server.join(3)
        if server.is_alive():
            kernel.CancelIoEx(handle, None)
            server.join(2)
        if server.is_alive():
            raise RuntimeError("Test pipe server did not stop.")
        if errors:
            raise errors[0]


class CommandPipeTests(unittest.TestCase):
    def test_native_message_reply_larger_than_one_buffer(self):
        response = ("telemetry-ready " * 9000).encode()
        with pipe_server(response) as (name, requests):
            self.assertEqual(command_pipe.request(name, "CarPackStudio status"), response.decode().strip())
            self.assertEqual(requests, [b"CarPackStudio status\n"])

    def test_reply_timeout_is_bounded_and_command_is_never_resent(self):
        with pipe_server(pause=0.25) as (name, requests):
            start = time.monotonic()
            with self.assertRaisesRegex(TimeoutError, "may have executed.*not retried"):
                command_pipe.request(name, "spawn car 363", timeout=0.08)
            self.assertLess(time.monotonic() - start, 0.6)
            self.assertEqual(requests, [b"spawn car 363\n"])

    def test_missing_endpoint_times_out_before_sending(self):
        start = time.monotonic()
        with self.assertRaisesRegex(TimeoutError, "no command was sent"):
            command_pipe.request("\\\\.\\pipe\\fh2mod-missing-" + uuid.uuid4().hex, "status", timeout=0.08)
        self.assertLess(time.monotonic() - start, 0.5)

    def test_busy_pipe_retries_only_open_not_command_write(self):
        native = command_pipe.kernel32.CreateFileW
        tries = []

        def busy_once(*args):
            tries.append(args[0])
            if len(tries) == 1:
                ctypes.set_last_error(command_pipe.ERROR_PIPE_BUSY)
                return command_pipe.INVALID_HANDLE_VALUE
            return native(*args)

        with pipe_server() as (name, requests), patch.object(command_pipe.kernel32, "CreateFileW", busy_once), \
                patch.object(command_pipe.kernel32, "WaitNamedPipeW", return_value=True):
            self.assertEqual(command_pipe.request(name, "status"), "ready")
            self.assertEqual(len(tries), 2)
            self.assertEqual(requests, [b"status\n"])

    def test_invalid_multiline_and_remote_pipe_rejected_before_io(self):
        for path, line in (("\\\\remote\\pipe\\fh2mod-1", "status"),
                           ("\\\\.\\pipe\\fh2mod-1", "status\nspawn clear"),
                           ("\\\\.\\pipe\\fh2mod-1", "")):
            with self.assertRaises(ValueError):
                command_pipe.request(path, line)

    def test_runtime_keeps_explicit_transport_errors_and_releases_lock(self):
        runtime = Runtime(dict(config.DEFAULTS))
        runtime.proc = SimpleNamespace(pid=12, poll=lambda: None)
        with patch.object(runtime, "session", return_value={"pid": 12, "pipe": "\\\\.\\pipe\\fh2mod-12"}), \
                patch("cps.runtime.pipe_request", side_effect=[TimeoutError("pipe busy"), "ready"]) as request:
            self.assertIn("connection unavailable", runtime.command("status"))
            self.assertEqual(runtime.command("status"), "ready")
            self.assertEqual(request.call_count, 2)


class WrapperControlsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        storage = patch.object(config, "APPDATA", self.directory.name)
        storage.start()
        self.addCleanup(storage.stop)
        with patch.object(Poller, "start"):
            self.wrapper = WrapperTab(dict(config.DEFAULTS), None)
        self.wrapper.timer.stop()
        self.wrapper.rt.proc = SimpleNamespace(pid=1234, poll=lambda: None)
        self.owner = SimpleNamespace(wrapper=self.wrapper, s=dict(config.DEFAULTS), log=Mock())
        self.widgets = []

    def tearDown(self):
        for widget in self.widgets:
            widget.close()
            widget.deleteLater()
        self.wrapper.rt.proc = None
        self.wrapper.shutdown()
        self.wrapper.overlay.deleteLater()
        self.wrapper.deleteLater()
        self.app.processEvents()

    def widget(self, cls):
        widget = cls(self.owner) if cls in (EventsPanel, MapsPanel, SpawnedPanel) else cls(self.wrapper)
        self.widgets.append(widget)
        return widget

    def test_event_stop_button_routes_exact_command_and_runtime_gates(self):
        panel = self.widget(EventsPanel)
        self.assertTrue(panel.stop_button.isEnabled())
        with patch.object(self.wrapper, "send") as send:
            panel.stop_button.click()
            send.assert_called_once_with("event stop")
        self.wrapper.running_changed.emit(False)
        self.assertFalse(panel.stop_button.isEnabled())

    def test_teleport_list_shares_map_sequence_and_loads_new_custom_entries(self):
        menu = QMenu()
        save_places([TeleportPlace("My place", 549, (1, 2, 3))])
        populate_places(menu, self.wrapper)
        action = next(action for action in menu.actions() if action.text() == "TheIsland (549) / My place [custom]")
        with patch.object(self.wrapper, "navigate") as navigate:
            action.trigger()
            navigate.assert_called_once_with(549, 'tp 549 "My place"')
        self.assertEqual(len(menu.actions()), 13)
        self.wrapper.rt.proc = None
        populate_places(menu, self.wrapper)
        self.assertTrue(all(not action.isEnabled() for action in menu.actions()))

    def test_my_places_table_exposes_map_name_xyz_adds_and_deletes_exact_map(self):
        save_places([TeleportPlace("Same", 542, (1, 2, 3)), TeleportPlace("Same", 549, (4, 5, 6))])
        panel = self.widget(MapsPanel)
        dialog = self.widget(PlacesDialog)
        self.assertEqual(dialog.table.columnCount(), 6)
        self.assertEqual(dialog.table.rowCount(), 2)
        self.assertEqual(dialog.table.item(1, 1).text(), "TheIsland")
        self.assertEqual([dialog.table.item(1, col).data(Qt.DisplayRole) for col in (3, 4, 5)], [4, 5, 6])
        dialog.table.setCurrentCell(1, 0)
        with patch("cps.travel_ui.QMessageBox.question", return_value=QMessageBox.Yes), \
                patch.object(self.wrapper.rt, "command") as command:
            dialog.delete_button.click()
            command.assert_not_called()
        self.assertEqual([(place.track_id, place.name) for place in load_places()], [(542, "Same")])
        position = "tp track=549 settled=1 queued=0 active=0 applied=0 position=123.25,45.5,-100"
        dialog.name.setText("New place")
        with patch.object(self.wrapper.rt, "command", return_value=position) as command:
            dialog.add_button.click()
            command.assert_called_once_with("CarPackStudio teleport capture")
        self.assertEqual(load_places()[-1], TeleportPlace("New place", 549, (123.25, 45.5, -100)))
        self.assertEqual(dialog.table.rowCount(), 2)
        self.assertEqual(panel.model.rowCount(), 2)

    def test_bookmark_dialog_errors_do_not_save_invalid_or_unsettled_positions(self):
        dialog = self.widget(PlacesDialog)
        for name, reply in (("Test", "error: wait for settled free roam"),
                            ("Bad\\name", "unused")):
            dialog.name.setText(name)
            with patch.object(self.wrapper.rt, "command", return_value=reply):
                dialog.add_current()
            self.assertEqual(load_places(), [])
            self.assertTrue(dialog.note.styleSheet())
        self.wrapper.rt.proc = None
        self.wrapper.running_changed.emit(False)
        self.assertFalse(dialog.add_button.isEnabled())

    def test_my_places_teleport_uses_map_qualified_identity(self):
        save_places([TeleportPlace("Same", 542, (1, 2, 3)), TeleportPlace("Same", 549, (4, 5, 6))])
        dialog = self.widget(PlacesDialog)
        dialog.table.setCurrentCell(1, 0)
        with patch.object(self.wrapper, "navigate") as navigate:
            dialog.teleport_button.click()
            navigate.assert_called_once_with(549, 'tp 549 "Same"')

    def test_console_line_detach_attach_preserves_input_history(self):
        console = self.wrapper.console
        console.inp.setText("draft")
        console.hist[:] = ["status"]
        console.detach_btn.click()
        self.assertIsNotNone(self.wrapper.console_window)
        self.assertEqual(console.detach_btn.accessibleName(), "Attach console")
        self.assertEqual(console.inp.text(), "draft")
        console.detach_btn.click()
        self.assertIsNone(self.wrapper.console_window)
        self.assertEqual(console.detach_btn.accessibleName(), "Detach console")
        self.assertEqual(console.hist, ["status"])

    def test_volume_accepts_decimal_case_insensitive_scoped_pid_and_no_pipe(self):
        with patch("cps.wrapper.process_volumes", return_value=[0.4]) as volumes, \
                patch.object(self.wrapper.rt, "command") as command:
            for value in ("0", "0.1", "0.4", "1"):
                self.wrapper.send("Volume " + value)
                volumes.assert_called_with(1234, float(value))
                self.assertEqual(self.wrapper.s["wrapper_volume"], float(value))
            command.assert_not_called()

    def test_invalid_volume_is_rejected_without_mutating_previous_setting(self):
        self.wrapper.s["wrapper_volume"] = 0.4
        with patch("cps.wrapper.process_volumes") as volumes:
            for value in ("", "-1", "1.1", "nan", "inf", "loud", "0.4 extra"):
                self.wrapper.send("volume " + value)
                self.assertEqual(self.wrapper.s["wrapper_volume"], 0.4)
            volumes.assert_not_called()
        for invalid in (True, None, [], float("nan"), -0.1, 1.01):
            with self.assertRaises(ValueError):
                validate_volume(invalid)

    def test_volume_saved_offline_and_applied_when_late_audio_session_appears(self):
        self.wrapper.rt.proc = None
        with patch("cps.wrapper.process_volumes", return_value=[0.1]) as volumes:
            self.wrapper.send("volume 0.1")
            volumes.assert_not_called()
        self.wrapper.rt.proc = SimpleNamespace(pid=5678, poll=lambda: None)
        self.wrapper._report_running()
        with patch("cps.wrapper.process_volumes", side_effect=[[], [0.1]]) as volumes:
            self.wrapper._apply_volume()
            self.assertEqual(self.wrapper._volume_pending, 5678)
            self.wrapper._volume_next_try = 0
            self.wrapper._apply_volume()
            self.assertIsNone(self.wrapper._volume_pending)
            volumes.assert_called_with(5678, 0.1)

    def test_volume_failure_is_reported_without_claiming_applied(self):
        with patch("cps.wrapper.process_volumes", side_effect=OSError("audio device removed")):
            self.wrapper.send("volume 0.4")
        self.assertIsNone(self.wrapper._volume_pending)
        self.assertIn("cannot set wrapper volume", self.wrapper.console.out.toPlainText())

    def test_repeated_pipe_errors_are_one_outage_and_controls_restore(self):
        panel = self.widget(SpawnedPanel)
        reply = "Studio spawned cars: 0/4, page 1/1 (use object ID for removal):"
        panel.on_snapshot({"spawn": reply})
        for error in ("error: pipe busy", "error: pipe missing", "error: pipe busy"):
            panel.on_snapshot({"spawn": error})
        self.assertEqual(self.owner.log.call_count, 1)
        self.assertFalse(panel.clear_button.isEnabled())
        self.assertIn("stale", panel.note.text())
        panel.on_snapshot({"spawn": reply})
        self.assertTrue(panel.clear_button.isEnabled())
        self.assertTrue(panel.available)
        self.assertIn("connection restored", self.wrapper.console.out.toPlainText())

    def test_transient_pipe_outage_keeps_navigation_but_never_fires_followup(self):
        self.wrapper.pending_navigation = (549, 'tp 549 "Docks"', time.monotonic())
        with patch.object(self.wrapper, "send") as send:
            self.wrapper.on_native_data({"pid": 1234, "track": "error: pipe missing", "spawn": "error: pipe missing"})
            self.assertIsNotNone(self.wrapper.pending_navigation)
            send.assert_not_called()

    def test_embedding_waits_for_actual_input_mod_window_discovery(self):
        self.wrapper._input_expected = {"FH2 Mouse & Keyboard", "NoPause"}
        self.assertFalse(self.wrapper._input_window_ready())
        self.wrapper._log_line("platform", "21:00:00 [FH2 Mouse & Keyboard] watching the game window (1280x720)")
        self.assertFalse(self.wrapper._input_window_ready())
        self.wrapper._log_line("platform", '21:00:01 [NoPause] watching game window 000123 (class Test, title "Game")')
        self.assertTrue(self.wrapper._input_window_ready())
        self.assertEqual(self.wrapper._input_ready, self.wrapper._input_expected)

    def test_input_discovery_timeout_is_reported_and_does_not_hang_startup(self):
        self.wrapper._input_expected = {"NoPause"}
        self.wrapper._input_wait_started = time.monotonic() - 21
        self.assertTrue(self.wrapper._input_window_ready())
        self.assertIn("Input initialization was not confirmed", self.wrapper.console.out.toPlainText())
        self.assertEqual(self.wrapper._input_expected, set())

    def test_input_handshake_is_not_satisfied_by_previous_process(self):
        with tempfile.TemporaryDirectory() as temp:
            mods = Path(temp) / "fh2-mods"
            mods.mkdir()
            (mods / "FH2Mouse.dll").touch()
            self.wrapper.rt.s = dict(self.wrapper.s, sandbox_dir=temp)
            self.wrapper._input_ready = {"FH2 Mouse & Keyboard"}
            self.wrapper._report_running()
            self.assertEqual(self.wrapper._input_expected, {"FH2 Mouse & Keyboard"})
            self.assertEqual(self.wrapper._input_ready, set())

    def test_wrapper_menu_contains_teleport_list_my_places_and_volume(self):
        settings = dict(config.DEFAULTS, mount=self.directory.name, sandbox_dir=self.directory.name)
        with patch("cps.app.config.load", return_value=settings), patch.object(Poller, "start"), \
                patch.object(MainWindow, "_startup"):
            window = MainWindow()
        window.wrapper.timer.stop()
        try:
            menu = next(action.menu() for action in window.menuBar().actions() if action.text() == "&Wrapper")
            self.assertIn("Volume...", [action.text() for action in menu.actions()])
            teleport = next(action.menu() for action in menu.actions() if action.text() == "&Teleport")
            self.assertEqual([action.text() for action in teleport.actions()], ["List", "My places..."])
            with patch("cps.app.QInputDialog.getDouble", return_value=(0.4, True)), \
                    patch.object(window.wrapper, "send") as send:
                window.wrapper_volume()
                send.assert_called_once_with("volume 0.400")
        finally:
            window.update_checker.shutdown()
            window.wrapper.shutdown()
            window.wrapper.overlay.deleteLater()
            window.deleteLater()


class FocusTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_native_focus_attaches_input_threads_and_detaches_after_setting_focus(self):
        with patch("cps.runtime.user32.GetForegroundWindow", return_value=100), \
                patch("cps.runtime.user32.GetAncestor", return_value=100), \
                patch("cps.runtime.user32.IsWindow", return_value=True), \
                patch("cps.runtime.user32.GetWindowThreadProcessId", return_value=20), \
                patch("cps.runtime.ctypes.windll.kernel32.GetCurrentThreadId", return_value=10), \
                patch("cps.runtime.user32.AttachThreadInput", return_value=True) as attach, \
                patch("cps.runtime.user32.SetForegroundWindow"), patch("cps.runtime.user32.SetActiveWindow"), \
                patch("cps.runtime.user32.SetFocus") as focus, \
                patch("cps.runtime.user32.GetFocus", return_value=200):
            self.assertTrue(focus_game_window(200, 100))
            focus.assert_called_once_with(200)
            self.assertEqual(attach.call_args_list[0].args, (10, 20, True))
            self.assertEqual(attach.call_args_list[1].args, (10, 20, False))

    def test_automatic_focus_never_steals_from_another_application(self):
        with patch("cps.runtime.user32.GetForegroundWindow", return_value=999), \
                patch("cps.runtime.user32.GetAncestor", return_value=999), \
                patch("cps.runtime.user32.IsWindow", return_value=True), \
                patch("cps.runtime.user32.SetFocus") as focus:
            self.assertFalse(focus_game_window(200, 100))
            focus.assert_not_called()

    def test_embed_refreshes_frame_and_schedules_native_focus(self):
        container = GameContainer()
        runtime = Mock()
        try:
            with patch("cps.wrapper.user32.GetWindowLongPtrW", return_value=0x10000000), \
                    patch("cps.wrapper.user32.SetParent") as parent, \
                    patch("cps.wrapper.user32.SetWindowLongPtrW"), \
                    patch("cps.wrapper.user32.SetWindowPos") as position, \
                    patch.object(container, "fit"), patch.object(container, "focus_game"), \
                    patch("cps.wrapper.QTimer.singleShot") as schedule:
                container.embed(runtime, 200)
                parent.assert_called_once_with(200, int(container.winId()))
                self.assertTrue(position.call_args.args[-1] & 0x20)
                schedule.assert_called_once()
                self.assertEqual(container.child, 200)
        finally:
            container.deleteLater()
