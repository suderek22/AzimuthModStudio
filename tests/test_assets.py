import io
import json
import os
import struct
import tempfile
import unittest
import uuid
import wave
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
from PIL import Image
from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QDialog, QPushButton, QTabWidget

from cps import config
from cps.app import MainWindow
from cps.assets import inspect_binary, strings, hex_page
from cps.audio import Sample, parse_fsb, read_bank, pcm_wav, xma_wav
from cps.audioviewer import BankViewer
from cps.editors import TextEditorTab
from cps.filetools import BinaryViewer, GameFilesManager
from cps.viewers import SwatchViewer
from cps.wrapper import Poller


def fsb_fixture(mode=2, channels=1, rate_code=9, frames=3, audio=b"\0\0\1\0\2\0"):
    header = bytearray(60)
    header[:4] = b"FSB5"
    names = struct.pack("<I", 4) + b"sample\0"
    struct.pack_into("<6I", header, 4, 1, 1, 8, len(names), len(audio), mode)
    word = (rate_code << 1) | ((channels - 1) << 5) | (frames << 34)
    return bytes(header) + struct.pack("<Q", word) + names + audio


def bank_fixture(fsb):
    body = b"FEV " + b"SND " + struct.pack("<I", len(fsb)) + fsb
    return b"RIFF" + struct.pack("<I", len(body)) + body


class AssetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_car_scene_header(self):
        name, skeleton = b"CAR", b"game:\\media\\cars\\CAR\\scene\\skeleton.modelbin"
        data = (struct.pack("<H", 5) + uuid.UUID(int=0).bytes_le + struct.pack("<BI", 1, 333) +
                struct.pack("<I", len(name)) + name + struct.pack("<I", len(skeleton)) + skeleton +
                struct.pack("<H", 0x7f))
        result = json.loads(inspect_binary("CAR.carbin", data))
        self.assertEqual(result["ordinal"], 333)
        self.assertEqual(result["scene_name"], "CAR")
        self.assertTrue(result["strict_build"])
        self.assertEqual(result["skeleton"], skeleton.decode())

    def test_lights_decodes_attachment_and_transform(self):
        path = b"game:\\body.modelbin"
        data = (struct.pack("<III", 0xDEADBEEF, 1, 1) + struct.pack("<I", len(path)) + path +
                struct.pack("<HI", 3, 1) + struct.pack("<III16f", 0x1234, 2, 0, *range(16)) +
                struct.pack("<I", 0))
        result = json.loads(inspect_binary("Lights.bin", data))
        self.assertEqual(result["attachments"][0]["bone_index"], 3)
        self.assertEqual(result["lights"][0]["id"], "0x00001234")
        self.assertEqual(result["lights"][0]["position"], [0, 1, 2, 3])
        self.assertEqual(result["remaining_bytes"], 4)

    def test_bundle_and_shader_container(self):
        payload = b"game:\\material\0"
        data = (b"burG" + struct.pack("<HHII", 1, 1, 40, 40 + len(payload)) + b"LTAM" +
                struct.pack("<BBHIIII", 1, 0, 0, 0, 40, len(payload), len(payload)) + payload)
        self.assertEqual(json.loads(inspect_binary("test.materialbin", data))["blobs"][0]["tag"], "MATL")
        payload = struct.pack("<II", 0x10050, 2)
        dxbc = b"DXBC" + bytes(16) + struct.pack("<4I", 1, 44 + len(payload), 1, 36)
        dxbc += b"SHDR" + struct.pack("<I", len(payload)) + payload
        result = json.loads(inspect_binary("test.vso", dxbc))
        self.assertEqual(result["chunks"][0]["stage"], "vertex")
        self.assertEqual(result["chunks"][0]["shader_model"], "5.0")
        with self.assertRaises(ValueError):
            inspect_binary("bad.pso", dxbc[:-1])

    def test_raw_formats_and_all_byte_pages_remain_accessible(self):
        data = b"physics_body\0" + "WideString".encode("utf-16-le") + bytes(70000)
        result = json.loads(inspect_binary("physicsdefinition.bin", data))
        self.assertIn("No verified semantic decoder", result["note"])
        viewer = BinaryViewer("physicsdefinition.bin", data)
        viewer.page.setValue(2)
        self.assertIn("00010000", viewer.hex.toPlainText())
        self.assertIn("physics_body", str(strings(data)[0]))
        self.assertEqual(hex_page(bytes([0x41, 0xff])), "00000000  41 FF                                            A.")
        viewer.deleteLater()

    def test_malformed_binary_reports_decode_error_without_disguising_as_text(self):
        viewer = BinaryViewer("bad.shaderbin", b"burG")
        tabs = viewer.findChild(QTabWidget)
        self.assertIn("Cannot decode structure", tabs.widget(0).toPlainText())
        viewer.deleteLater()
        widget = MainWindow._asset_widget("binary.xml", b"LDFB" + bytes(32))
        self.assertIsInstance(widget, BinaryViewer)
        widget.deleteLater()

    def test_avpins_dispatches_as_xml_and_archive_is_read_only(self):
        widget = MainWindow._asset_widget("car.avpins", b"<PointsOfInterest/>")
        self.assertIsInstance(widget, TextEditorTab)
        self.assertEqual(widget.kind, "xml")
        self.assertTrue(widget.editor.isReadOnly())
        self.assertFalse(widget.save_btn.isEnabled())
        widget.deleteLater()
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp, "assets.zip")
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("car.avpins", b"<PointsOfInterest/>")
                archive.writestr("mesh.materialbin", b"burG")
                archive.writestr("ignored.exe", b"no")
            manager = GameFilesManager(temp)
            manager.load_archive(str(path))
            self.assertEqual(manager.entries.rowCount(), 2)
            calls = []
            manager.open_requested.connect(lambda path, entry: calls.append((path, entry)))
            manager._open_entry(manager.proxy.index(0, 1))
            self.assertEqual(calls, [(str(path), "car.avpins")])
            manager.deleteLater()

    def test_tools_menu_opens_one_reusable_game_files_manager(self):
        with tempfile.TemporaryDirectory() as temp:
            settings = dict(config.DEFAULTS, mount=temp)
            with patch("cps.app.config.load", return_value=settings), \
                    patch.object(Poller, "start"), patch.object(MainWindow, "_startup"):
                window = MainWindow()
                window.wrapper.timer.stop()
                try:
                    tools = next(action.menu() for action in window.menuBar().actions()
                                 if action.text() == "&Tools")
                    action = next(action for action in tools.actions() if action.text() == "Game files manager")
                    action.trigger()
                    manager = window.tabs.currentWidget()
                    self.assertIsInstance(manager, GameFilesManager)
                    self.assertEqual(manager.location.text(), temp)
                    action.trigger()
                    self.assertIs(window.tabs.currentWidget(), manager)
                    self.assertEqual(sum(isinstance(window.tabs.widget(i), GameFilesManager)
                                         for i in range(window.tabs.count())), 1)
                finally:
                    window.wrapper.shutdown()
                    window.wrapper.overlay.deleteLater()
                    window.deleteLater()

    def test_shared_loose_and_archive_tabs_deduplicate_and_save(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp, "sample.txt")
            path.write_text("before")
            owner = SimpleNamespace(tabs=QTabWidget(), _asset_widget=MainWindow._asset_widget)
            MainWindow.open_asset_path(owner, str(path))
            self.assertEqual(owner.tabs.count(), 1)
            widget = owner.tabs.widget(0)
            widget.editor.setPlainText("after")
            widget.save()
            self.assertEqual(path.read_text(), "after")
            MainWindow.open_asset_path(owner, str(path))
            self.assertEqual(owner.tabs.count(), 1)
            archive_path = Path(temp, "assets.zip")
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("sample.txt", "archive")
            MainWindow.open_asset_path(owner, str(archive_path), "sample.txt")
            self.assertEqual(owner.tabs.count(), 2)
            self.assertTrue(owner.tabs.widget(1).editor.isReadOnly())
            owner.tabs.deleteLater()

    def test_fsb_bank_roundtrip_pcm_and_xma_wrapper(self):
        samples = read_bank(bank_fixture(fsb_fixture()))
        self.assertEqual(len(samples), 1)
        sample = samples[0]
        self.assertEqual((sample.name, sample.rate, sample.channels, sample.frames), ("sample", 48000, 1, 3))
        with wave.open(io.BytesIO(pcm_wav(sample)), "rb") as wav:
            self.assertEqual(wav.getnframes(), 3)
            self.assertEqual(wav.readframes(3), b"\0\0\1\0\2\0")
        wrapped = xma_wav(Sample("test", 48000, 1, 100, 10, bytes(2048)))
        self.assertTrue(wrapped.startswith(b"RIFF"))
        self.assertIn(b"WAVEfmt ", wrapped)

    def test_bank_errors_are_explicit(self):
        event_only = b"RIFF" + struct.pack("<I", 4) + b"FEV "
        with self.assertRaisesRegex(ValueError, "event data only"):
            read_bank(event_only)
        for data in (b"invalid", bank_fixture(fsb_fixture())[:-4], b"FSB5" + bytes(30)):
            with self.assertRaises(ValueError):
                read_bank(data)
        with self.assertRaisesRegex(ValueError, "Truncated PCM16"):
            pcm_wav(Sample("bad", 48000, 1, 100, 2, b""))
        with self.assertRaises(ValueError):
            parse_fsb(fsb_fixture(rate_code=15))

    def test_bank_viewer_exports_pcm_and_cleans_temp(self):
        viewer = BankViewer("test.bank", bank_fixture(fsb_fixture()))
        directory = viewer.temp.name
        try:
            with tempfile.TemporaryDirectory() as temp:
                out = Path(temp, "sample.wav")
                with patch("cps.audioviewer.QFileDialog.getSaveFileName", return_value=(str(out), "WAV")):
                    viewer.request("export")
                with wave.open(str(out), "rb") as wav:
                    self.assertEqual(wav.getnframes(), 3)
            viewer.stop()
        finally:
            viewer.shutdown()
            viewer.deleteLater()
        self.assertFalse(os.path.exists(directory))

    def test_unsupported_bank_codec_does_not_export_fake_wav(self):
        viewer = BankViewer("test.bank", bank_fixture(fsb_fixture(mode=15)))
        try:
            with patch.object(viewer, "_error") as error, \
                    patch("cps.audioviewer.QFileDialog.getSaveFileName") as dialog:
                viewer.request("export")
                self.assertIn("not supported", error.call_args[0][0])
                dialog.assert_not_called()
                self.assertIsNone(viewer.ready)
        finally:
            viewer.shutdown()
            viewer.deleteLater()

    def test_swatch_png_export_is_original_rgba_not_channel_preview(self):
        rgba = np.array([[[255, 31, 47, 128], [20, 40, 60, 255]]], dtype=np.uint8)
        texture = SimpleNamespace(width=2, height=1, format="RGBA8", mips=1, rgba=lambda: rgba)
        with patch("cps.swatch.load_texture", return_value=texture):
            viewer = SwatchViewer("sample.SWATCHBIN", b"fake")
        viewer.chk["R"].setChecked(False)
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp, "sample.png")
            with patch("cps.viewers.QFileDialog.getSaveFileName", return_value=(str(path), "PNG")):
                viewer.export_png()
            np.testing.assert_array_equal(np.array(Image.open(path)), rgba)
        viewer.deleteLater()

    def test_swatch_decode_and_export_failures_are_reported(self):
        with patch("cps.swatch.load_texture", side_effect=ValueError("unsupported format")):
            viewer = SwatchViewer("bad.swatchbin", b"bad")
        export = next(b for b in viewer.findChildren(QPushButton) if b.text().startswith("Export"))
        self.assertFalse(export.isEnabled())
        with patch("cps.viewers.QMessageBox.warning") as error:
            viewer.export_png()
            self.assertIn("unsupported format", error.call_args[0][2])
        viewer.deleteLater()
        rgba = np.zeros((1, 1, 4), dtype=np.uint8)
        texture = SimpleNamespace(width=1, height=1, format="RGBA8", mips=1, rgba=lambda: rgba)
        with patch("cps.swatch.load_texture", return_value=texture):
            viewer = SwatchViewer("sample.swatchbin", b"test")
        with tempfile.TemporaryDirectory() as temp:
            with patch("cps.viewers.QFileDialog.getSaveFileName", return_value=(temp, "PNG")), \
                    patch("cps.viewers.QMessageBox.warning") as error:
                viewer.export_png()
                self.assertIn("Cannot write PNG", error.call_args[0][2])
        viewer.deleteLater()

    def test_bundled_icon_sizes_alpha_and_popup_inheritance(self):
        path = Path(config.ROOT, "resources", "app.ico")
        icon = QIcon(str(path))
        self.assertFalse(icon.isNull())
        with Image.open(path) as image:
            self.assertTrue({(16, 16), (32, 32), (48, 48), (256, 256)}.issubset(image.ico.sizes()))
        old = self.app.windowIcon()
        try:
            self.app.setWindowIcon(icon)
            dialog = QDialog()
            self.assertEqual(dialog.windowIcon().cacheKey(), icon.cacheKey())
            dialog.deleteLater()
        finally:
            self.app.setWindowIcon(old)


if __name__ == "__main__":
    unittest.main()
