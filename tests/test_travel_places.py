import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cps import config
from cps.travel import (ISLAND_DESTINATIONS, TeleportPlace, load_places, save_places, parse_player_position,
                        resolve_destination, resolve_place_track, teleport_entries, validate_place_name)


class TravelPlaceTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        patcher = patch.object(config, "APPDATA", directory.name)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.path = Path(directory.name) / "teleport_places.json"

    def test_island_has_real_hub_and_landmarks(self):
        self.assertEqual(set(ISLAND_DESTINATIONS), {"Festival", "Car Meet", "Docks", "Barn Find"})
        for alias in ("Festival", "outpost", "garage"):
            self.assertEqual(resolve_destination("", "TheIsland", alias),
                             ("Festival", (9218.77, 139.458, -2889.03)))
        self.assertEqual(resolve_destination("", "549", "car meet")[1], (8837.37, 119.409, -3432.21))
        self.assertEqual(resolve_destination("", "island", "Docks")[1], (8729.18, 86.49, -1133.23))
        self.assertEqual(resolve_destination("", "island", "Barn Find")[1], (9934.19, 180.944, -4557.42))
        for name in ("Speed Trap 1", "Speed Zone 1 Start", "Speed Zone 1 End"):
            with self.assertRaises(ValueError):
                resolve_destination("", "island", name)

    def test_places_round_trip_and_custom_entries_are_last(self):
        self.assertEqual(load_places(), [])
        places = [TeleportPlace("My place", 549, (1, 2, 3)), TeleportPlace("My place", 7777, (4, 5, 6))]
        save_places(places)
        self.assertEqual(load_places(), places)
        self.assertEqual(teleport_entries()[-2:], ['tp TheIsland "My place" [custom]', 'tp 7777 "My place" [custom]'])
        self.assertEqual(resolve_place_track("7777"), ("7777", 7777))
        self.assertEqual(list(self.path.parent.glob("*.tmp")), [])

    def test_rejects_bad_names_and_unsettled_or_invalid_player_position(self):
        for name in ("", "a" * 81, "list", "STATUS", "hello\nworld", '"quoted"', "a\\b"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                validate_place_name(name)
        self.assertEqual(parse_player_position(
            "tp track=549 settled=1 queued=0 active=0 applied=1 position=1.5,-2,3"), (549, (1.5, -2, 3)))
        for reply in ("error: no player", "tp track=549 settled=0 queued=0 active=0 applied=0 position=1,2,3",
                      "tp track=549 settled=1 queued=1 active=0 applied=0 position=1,2,3",
                      "tp track=549 settled=1 queued=0 active=0 applied=0 position=nan,2,3"):
            with self.subTest(reply=reply), self.assertRaises(ValueError):
                parse_player_position(reply)

    def test_corrupt_storage_is_not_silently_discarded(self):
        invalid = [[], {"version": 2, "places": []}, {"version": 1, "places": [{}]}]
        for track, pos in ((True, [1, 2, 3]), (0, [1, 2, 3]), (549, [True, 2, 3]),
                           (549, [1e100, 2, 3]), (549, [1, 2])):
            invalid.append({"version": 1, "places": [{"name": "Test", "track_id": track, "position": pos}]})
        for data in invalid:
            self.path.write_text(json.dumps(data), encoding="utf-8")
            with self.subTest(data=data), self.assertRaises(ValueError):
                load_places()
        self.path.write_text("invalid json", encoding="utf-8")
        with self.assertRaises(ValueError):
            load_places()

    def test_failed_save_preserves_previous_file(self):
        save_places([TeleportPlace("Previous", 542, (1, 2, 3))])
        before = self.path.read_bytes()
        with patch("cps.travel.os.replace", side_effect=OSError("disk denied")), self.assertRaises(OSError):
            save_places([TeleportPlace("New", 549, (4, 5, 6))])
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(list(self.path.parent.glob("*.tmp")), [])
