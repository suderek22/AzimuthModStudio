import copy
import struct
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from cps.lod import mesh_lod
from cps.scripts import validate_script, encode_recording, decode_recording, FRAME
from cps.travel import resolve_destination, DESTINATIONS
from cps.viewers import ModelViewer


def script_fixture():
    def frame(ms, buttons=0, **axes):
        return dict(ms=ms, buttons=buttons, lt=0, rt=0, lx=0, ly=0, rx=0, ry=0) | axes
    return {"format": "CPS-XInput", "version": 1,
            "frames": [frame(0, 0x1000), frame(50, 0xffff & 0xf3ff, rt=1, lx=-1, ry=1), frame(100)]}


class TestbenchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_controller_transport_roundtrip_and_xinput_wgi_mapping(self):
        script = script_fixture()
        data = encode_recording(script)
        self.assertEqual(len(data), 12 + 3 * FRAME.size)
        self.assertEqual(struct.unpack_from("<II", data, 12), (0, 4))  # XInput A -> WGI A
        self.assertEqual(decode_recording(data), script)

    def test_controller_validation_rejects_bad_frames_and_held_final_controls(self):
        mutations = [
            ("ms", -1, 0), ("ms", True, 0), ("ms", 1800001, 2), ("ms", 40, 2),
            ("buttons", 0x400, 0), ("buttons", -1, 0),
            ("lt", -0.1, 0), ("rt", float("nan"), 0), ("lx", 1.1, 0), ("ly", True, 0),
            ("lt", 10 ** 400, 0),
            ("buttons", 0x1000, 2), ("rx", 1, 2),
        ]
        for field, value, index in mutations:
            with self.subTest(field=field, value=value, index=index):
                script = copy.deepcopy(script_fixture())
                script["frames"][index][field] = value
                with self.assertRaises(ValueError):
                    validate_script(script)
                script = script_fixture()
                script["version"] = True
                with self.assertRaises(ValueError):
                    validate_script(script)
        script = script_fixture()
        del script["frames"][0]["rt"]
        with self.assertRaises(ValueError):
            validate_script(script)

    def test_controller_transport_rejects_truncation_trailing_data_and_unknown_buttons(self):
        good = encode_recording(script_fixture())
        invalid = [b"", good[:-1], good + b"x", b"XXXX" + good[4:]]
        changed = bytearray(good)
        struct.pack_into("<I", changed, 16, 0x4000)
        invalid.append(bytes(changed))
        for data in invalid:
            with self.assertRaises(ValueError):
                decode_recording(data)

    def test_all_destinations_are_loaded_from_named_locators_not_entity_fallbacks(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "media" / "tracks" / "anthem" / "trackroutes" / "Route0.nt"
            path.parent.mkdir(parents=True)
            xml = "<!-- legacy comment: \u2022 -->\n<TrackLocators>"
            for index, (locator, _) in enumerate(DESTINATIONS.values()):
                if not isinstance(locator, str):
                    continue
                xml += ("<Locator><Name value='%s'/><SceneTransform value._41=\"%d'\" "
                        "value._42='2' value._43='-3'/></Locator>") % (locator, index)
            xml += "</TrackLocators><OtherSection/>"
            path.write_bytes(xml.encode("cp1252"))
            for index, name in enumerate(DESTINATIONS):
                expected = (425.574, 110.821, -1478.082) if name == "Airport" else (index, 2, -3)
                self.assertEqual(resolve_destination(directory, "anthem", name), (name, expected))
            self.assertEqual(resolve_destination(directory, "Anthem", "Nice")[0], "Nicea")
            self.assertEqual(resolve_destination(directory, "Anthem", "San Giovanni")[0], "San Giovanni")
            for map_name, place in (("TheIsland", "unknown"), ("unknown", "festival"), ("Anthem", "unknown")):
                with self.assertRaises(ValueError):
                    resolve_destination(directory, map_name, place)
            path.write_text("<TrackLocators/>", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Expected one locator"):
                resolve_destination(directory, "Anthem", "Festival")

    def test_airport_uses_exact_coordinates_without_requiring_locator_files(self):
        self.assertIn("Airport", DESTINATIONS)
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(resolve_destination(directory, "Anthem", " AIRPORT "),
                             ("Airport", (425.574, 110.821, -1478.082)))
            with self.assertRaises(ValueError):
                resolve_destination(directory, "TheIsland", "Airport")

    def test_lod_filter_respects_mesh_checkboxes_and_unprefixed_parts(self):
        self.assertEqual(mesh_lod("engineBay_a_LOD0"), "LOD0")
        self.assertEqual(mesh_lod("engineBay_a_LODS"), "LODS")
        self.assertEqual(mesh_lod("wheel_lod10"), "LOD10")
        self.assertIsNone(mesh_lod("engineBay_a_LOD0word"))
        self.assertEqual(mesh_lod("lodS_body"), "LODS")
        self.assertEqual(mesh_lod("LOD10_wheel"), "LOD10")
        self.assertIsNone(mesh_lod("not_LOD0_body"))
        self.assertIsNone(mesh_lod("LOD0word"))
        meshes = [SimpleNamespace(name=name, pos=np.zeros((3, 3)), nrm=None, material=0,
                                  tris=np.array([[0, 1, 2]]))
                  for name in ("engineBay_a_LOD0", "engineBay_a_LOD1", "engineBay_a_LODS", "glass")]
        with patch("cps.model.load_model", return_value=SimpleNamespace(meshes=meshes, materials=["paint"], notes=[])):
            viewer = ModelViewer("test.modelbin", b"")
        self.assertEqual([viewer.lod.itemText(i) for i in range(viewer.lod.count())],
                         ["All LODs", "LOD0", "LOD1", "LODS"])
        viewer.list.item(3).setCheckState(Qt.Unchecked)
        viewer.lod.setCurrentText("LOD1")
        self.assertEqual([m["visible"] for m in viewer.meshes], [False, True, False, False])
        self.assertTrue(viewer.list.item(0).isHidden())
        self.assertFalse(viewer.list.item(3).isHidden())
        viewer._set_all(True)
        self.assertEqual([m["visible"] for m in viewer.meshes], [False, True, False, True])
        viewer.lod.setCurrentIndex(0)
        self.assertTrue(all(m["visible"] for m in viewer.meshes))
        viewer.deleteLater()


if __name__ == "__main__":
    unittest.main()
