import configparser
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, Mock

from PySide6.QtWidgets import QApplication, QDockWidget, QTextBrowser, QMainWindow, QCheckBox, QTabBar
from PySide6.QtCore import Qt, QPoint, QPointF, QEvent
from PySide6.QtGui import QPalette, QMouseEvent
from PySide6.QtTest import QTest

from cps import config, theme
from cps.app import MainWindow, SettingsDialog, CREDITS
from cps.wrapper import WrapperTab, Poller
from cps.runtime import Runtime


class AlphaUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        theme.apply(cls.app)

    def test_dragging_individual_dock_tabs_to_edges_and_outside(self):
        with patch("cps.app.config.load", return_value=dict(config.DEFAULTS)), \
                patch.object(MainWindow, "_startup"):
            window = MainWindow()
        try:
            window.show()
            self.app.processEvents()
            docks = {dock.objectName(): dock for dock in window.findChildren(QDockWidget)}
            cases = [("Mods", Qt.RightDockWidgetArea), ("Events", Qt.TopDockWidgetArea)]
            # Floating mouse capture needs a native window system, not Qt's offscreen plugin.
            if self.app.platformName() != "offscreen":
                cases.append(("Maps", None))
            for name, area in cases:
                with self.subTest(name=name):
                    bar = next(bar for bar in window.findChildren(QTabBar)
                               if bar.parent() is window and name in
                               [bar.tabText(i) for i in range(bar.count())])
                    index = next(i for i in range(bar.count()) if bar.tabText(i) == name)
                    bar.setCurrentIndex(index)
                    self.app.processEvents()
                    center = window.centralWidget().geometry()
                    if area == Qt.RightDockWidgetArea:
                        drop = window.mapToGlobal(QPoint(center.right() - 3, center.center().y()))
                    elif area == Qt.TopDockWidgetArea:
                        drop = window.mapToGlobal(QPoint(center.center().x(), center.top() + 3))
                    else:
                        drop = window.mapToGlobal(QPoint(window.width() + 80, 150))
                    QTest.mousePress(bar, Qt.LeftButton, Qt.NoModifier, bar.tabRect(index).center())
                    self.app.sendEvent(bar, QMouseEvent(
                        QEvent.MouseMove, QPointF(bar.mapFromGlobal(drop)), QPointF(drop),
                        Qt.NoButton, Qt.LeftButton, Qt.NoModifier))
                    self.app.processEvents()
                    self.assertTrue(window._dock_tab_drag[3])
                    QTest.mouseRelease(bar, Qt.LeftButton, Qt.NoModifier, bar.mapFromGlobal(drop))
                    self.app.processEvents()
                    self.assertIsNone(window._dock_tab_drag)
                    self.assertNotIn(docks[name], window.tabifiedDockWidgets(docks["Explorer"]))
                    self.assertEqual(docks[name].isFloating(), area is None)
                    if area is not None:
                        self.assertEqual(window.dockWidgetArea(docks[name]), area)
                    self.assertFalse(docks["Cars"].isFloating())
            self.assertEqual(window.palette().color(QPalette.Window).name(), theme.BG.lower())
            self.assertIn(theme.BG, self.app.styleSheet())
        finally:
            with patch("cps.app.config.save"):
                window.close()
            window.update_checker.shutdown()
            window.wrapper.shutdown()
            window.wrapper.overlay.deleteLater()
            window.deleteLater()
            self.app.processEvents()
            self.app.sendPostedEvents(None, QEvent.DeferredDelete)

    def test_settings_cannot_disable_the_shipped_sandbox_save_template(self):
        settings = SettingsDialog(dict(config.DEFAULTS, ultimate_save=False))
        try:
            self.assertTrue(settings.result_settings()["ultimate_save"])
            self.assertFalse(any("ultimate save" in box.text().lower()
                                 for box in settings.findChildren(QCheckBox)))
        finally:
            settings.deleteLater()

    def test_dock_menu_can_hide_and_restore_every_panel_with_pack_hidden_by_default(self):
        with patch("cps.app.config.load", return_value=dict(config.DEFAULTS)), \
                patch.object(MainWindow, "_startup"):
            window = MainWindow()
        try:
            window.show()
            self.app.processEvents()
            docks = {dock.objectName(): dock for dock in window.findChildren(QDockWidget)}
            self.assertTrue(window.dockOptions() & QMainWindow.AllowNestedDocks)
            self.assertFalse(window.dockOptions() & QMainWindow.GroupedDragging)
            self.assertTrue(docks["Pack"].isHidden())
            self.assertTrue(docks["Car properties"].isHidden())
            menu = window.createPopupMenu()
            actions = {action.text(): action for action in menu.actions()}
            for name, dock in docks.items():
                self.assertTrue(dock.features() & QDockWidget.DockWidgetClosable)
                self.assertTrue(actions[name].isEnabled())
                original = dock.isHidden()
                actions[name].trigger()
                self.assertEqual(dock.isHidden(), not original, name)
                actions[name].trigger()
                self.assertEqual(dock.isHidden(), original, name)
            menu.deleteLater()
            mods = docks["Mods"]
            mods.setFloating(True)
            self.assertTrue(mods.isFloating())
            mods.setFloating(False)
            window.addDockWidget(Qt.RightDockWidgetArea, mods)
            self.assertEqual(window.dockWidgetArea(mods), Qt.RightDockWidgetArea)
            window.addDockWidget(Qt.TopDockWidgetArea, mods)
            self.assertEqual(window.dockWidgetArea(mods), Qt.TopDockWidgetArea)
            self.assertNotIn(mods, window.tabifiedDockWidgets(docks["Explorer"]))
            self.assertTrue(all(box.text() != "Travel test" for box in window.wrapper.findChildren(QCheckBox)))
            with patch("cps.app.config.save") as save:
                window.travel_binds_action.setChecked(False)
                self.assertFalse(window.s["travel_test_binds"])
                save.assert_called_once_with(window.s)
            window.wrapper.running_changed.emit(True)
            self.assertFalse(window.travel_binds_action.isEnabled())
            window.wrapper.running_changed.emit(False)
            self.assertTrue(window.travel_binds_action.isEnabled())
        finally:
            window.update_checker.shutdown()
            window.wrapper.shutdown()
            window.wrapper.overlay.deleteLater()
            window.deleteLater()
            self.app.processEvents()

    def test_credits_end_at_attribution_and_links_are_clickable(self):
        tail = CREDITS.split("It contains no exported game pixels.", 1)[1]
        self.assertNotIn("Tools", tail)
        self.assertNotIn("Console", tail)
        self.assertIn('href="https://github.com/suderek22"', tail)
        self.assertIn('href="https://fh2mods.com/"', tail)
        with patch("cps.app.QDialog.exec") as execute:
            def inspect():
                browser = next(widget for widget in self.app.allWidgets()
                               if isinstance(widget, QTextBrowser) and widget.toPlainText().startswith("Credits"))
                self.assertTrue(browser.openExternalLinks())
            execute.side_effect = inspect
            owner = None
            MainWindow.credits(owner)

    def test_square_detach_icons_and_separate_binds_setting_drives_native_ini(self):
        settings = dict(config.DEFAULTS)
        with patch.object(Poller, "start"):
            wrapper = WrapperTab(settings, None)
        wrapper.timer.stop()
        try:
            for button in (wrapper.detach_btn, wrapper.console.detach_btn):
                self.assertEqual(button.width(), button.height())
                self.assertFalse(button.text())
                self.assertFalse(button.icon().isNull())
                self.assertIn("Detach", button.accessibleName())
            wrapper._set_game_detached(True)
            self.assertEqual(wrapper.detach_btn.accessibleName(), "Attach game window")
            wrapper.console.detach_btn.click()
            self.assertEqual(wrapper.console.detach_btn.accessibleName(), "Attach console")
            wrapper.console.detach_btn.click()
            self.assertEqual(wrapper.console.detach_btn.accessibleName(), "Detach console")
            with tempfile.TemporaryDirectory() as temp:
                rt = Runtime(dict(settings, sandbox_dir=temp, travel_test_binds=False,
                                  experimental_live_reload=False))
                rt._write_loader_ini()
                ini = configparser.ConfigParser()
                ini.read(Path(rt.mods_dir) / "CarPackStudio.ini")
                self.assertEqual(ini.getint("Car", "ExperimentalReload"), 1)
                self.assertEqual(ini.getint("Car", "TravelTestBinds"), 0)
                rt.s["travel_test_binds"] = True
                rt._write_loader_ini()
                ini.read(Path(rt.mods_dir) / "CarPackStudio.ini")
                self.assertEqual(ini.getint("Car", "ExperimentalReload"), 1)
                self.assertEqual(ini.getint("Car", "TravelTestBinds"), 1)
        finally:
            wrapper.rt.proc = None
            wrapper.shutdown()
            wrapper.overlay.deleteLater()
            wrapper.deleteLater()
            self.app.processEvents()

    def test_radio_mode_launch_protects_overrides_and_keeps_command_flow(self):
        settings = dict(config.DEFAULTS, travel_test_binds=False)
        rt = Runtime(settings)
        with patch("cps.runtime.require_dependencies"), patch("cps.runtime.subprocess.Popen"), \
                patch.object(rt, "_restore_swap_flow") as restore, patch.object(rt, "_backup_swap_flow") as backup:
            rt.launch()
            restore.assert_called_once()
            backup.assert_called_once()

    def test_radio_mode_restores_actual_prior_radio_flow_before_launch(self):
        for original_exists in (True, False):
            with self.subTest(original_exists=original_exists), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                flow = root / "mediaoverride" / "stateflow" / "pause.xml"
                flow.parent.mkdir(parents=True)
                original = b'<state id="radio_left_flow">original radio</state>'
                if original_exists:
                    flow.write_bytes(original)
                rt = Runtime(dict(config.DEFAULTS, mount=str(root), travel_test_binds=False))
                rt._backup_swap_flow()
                flow.write_bytes(b"experimental flow")
                rt._restore_swap_flow()
                if original_exists:
                    self.assertEqual(flow.read_bytes(), original)
                else:
                    self.assertFalse(flow.exists())
                self.assertFalse(Path(str(flow) + ".cps_orig").exists())
                self.assertFalse(Path(str(flow) + ".cps_absent").exists())
