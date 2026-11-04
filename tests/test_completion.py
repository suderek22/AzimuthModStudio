import tempfile
import unittest
from unittest.mock import patch

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from cps import config
from cps.completion import command_candidates
from cps.console import Console
from cps.travel import TeleportPlace, save_places


class CompletionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        patcher = patch.object(config, "APPDATA", self.temp.name)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.console = Console()
        self.console.show()
        self.console.inp.setFocus()
        self.app.processEvents()
        self.addCleanup(self.console.deleteLater)

    def key(self, key, modifiers=Qt.NoModifier):
        QTest.keyClick(self.console.inp, key, modifiers)

    def test_tab_completes_subcommands_without_submitting_or_moving_focus(self):
        submitted = []
        self.console.submit.connect(submitted.append)
        self.console.inp.setText("eve")
        self.key(Qt.Key_Tab)
        self.assertEqual(self.console.inp.text(), "event ")
        self.console.inp.setText("event st")
        self.key(Qt.Key_Tab)
        self.assertEqual(self.console.inp.text(), "event start ")
        self.key(Qt.Key_Tab)
        self.assertEqual(self.console.inp.text(), "event status")
        self.key(Qt.Key_Tab, Qt.ShiftModifier)
        self.assertEqual(self.console.inp.text(), "event start ")
        self.assertTrue(self.console.inp.hasFocus())
        self.assertEqual(submitted, [])
        self.assertEqual(self.console.hist, [])

    def test_single_candidate_case_insensitive_and_history_completion(self):
        self.console.inp.setText("VOLUME 0.4")
        self.key(Qt.Key_Tab)
        self.assertEqual(self.console.inp.text(), "volume 0.4")
        self.console.hist.append("spawn car 363")
        self.console.inp.setText("spawn car 3")
        self.key(Qt.Key_Tab)
        self.assertEqual(self.console.inp.text(), "spawn car 363")
        self.key(Qt.Key_Return)
        self.assertEqual(self.console.hist[-1], "spawn car 363")

    def test_quoted_bookmarks_maps_and_new_candidates_refresh(self):
        save_places([TeleportPlace("My garage", 549, (1, 2, 3))])
        self.assertIn('tp TheIsland "Docks"', command_candidates())
        self.console.inp.setText('tp 549 "My')
        self.key(Qt.Key_Tab)
        self.assertEqual(self.console.inp.text(), 'tp 549 "My garage"')
        self.console.inp.setText("tp rem")
        self.key(Qt.Key_Tab)
        self.assertEqual(self.console.inp.text(), "tp remove ")
        self.key(Qt.Key_Tab)
        self.key(Qt.Key_Tab)
        self.assertEqual(self.console.inp.text(), 'tp remove "My garage"')

    def test_reset_after_edit_reverse_wrap_and_no_match_preserves_draft(self):
        self.console.inp.setText("freeze ")
        self.key(Qt.Key_Tab, Qt.ShiftModifier)
        self.assertEqual(self.console.inp.text(), "freeze 1")
        self.key(Qt.Key_Tab)
        self.assertEqual(self.console.inp.text(), "freeze 0")
        self.console.inp.setText("weather of")
        self.key(Qt.Key_Tab)
        self.assertEqual(self.console.inp.text(), "weather off")
        self.console.inp.setText("no such command")
        self.key(Qt.Key_Tab)
        self.assertEqual(self.console.inp.text(), "no such command")
        self.console.inp.setText("event stop")
        self.console.inp.setCursorPosition(2)
        self.key(Qt.Key_Tab)
        self.assertEqual(self.console.inp.text(), "event stop")
        self.assertEqual(self.console.inp.cursorPosition(), 2)

    def test_completion_storage_error_is_visible_and_does_not_change_input(self):
        self.console.completion_provider = lambda: (_ for _ in ()).throw(ValueError("invalid bookmark file"))
        self.console.inp.setText("tp ")
        self.key(Qt.Key_Tab)
        self.assertEqual(self.console.inp.text(), "tp ")
        self.assertIn("Cannot load console completions: invalid bookmark file", self.console.out.toPlainText())

    def test_history_draft_navigation_still_works_after_completion(self):
        self.console.hist[:] = ["status"]
        self.console.hi = 1
        self.console.inp.setText("eve")
        self.key(Qt.Key_Tab)
        draft = self.console.inp.text()
        self.key(Qt.Key_Up)
        self.assertEqual(self.console.inp.text(), "status")
        self.key(Qt.Key_Down)
        self.assertEqual(self.console.inp.text(), draft)
