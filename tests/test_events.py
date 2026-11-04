import sqlite3
import tempfile
import unittest
from pathlib import Path
from contextlib import closing
from unittest.mock import patch

from cps.gamedata import GameData


class EventDataTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.mount = Path(directory.name)
        self.path = self.mount / "media" / "stripped" / "gamedbRC.slt"
        self.path.parent.mkdir(parents=True)
        self.create_database(self.path, "Base race")

    @staticmethod
    def create_database(path, title):
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(path)) as db:
            db.executescript(
                "CREATE TABLE AnthemEventRaces(AnthemEventRaceId INTEGER, AnthemEventId INTEGER, TrackId INTEGER, NumLaps INTEGER);"
                "CREATE TABLE AnthemEvents(AnthemEventId INTEGER, Name TEXT, EventType INTEGER);"
                "CREATE TABLE Tracks(id INTEGER, DisplayName TEXT, MediaTrack INTEGER, MediaName TEXT, Route INTEGER);"
                "INSERT INTO AnthemEvents VALUES(10,'Event',0),(20,'Showcase',2);"
                "INSERT INTO Tracks VALUES(999,'Island race',549,'Test03',13),(998,'Showcase track',542,'Anthem',14),"
                "(997,'Unsupported map',7777,'Custom',15);"
                "INSERT INTO AnthemEventRaces VALUES(1,10,996,3),(2,10,999,1),(3,20,998,1),(4,10,997,1);"
            )
            db.execute("INSERT INTO Tracks VALUES(996,?,542,'Anthem',12)", (title,))
            db.commit()

    def test_race_ids_map_metadata_and_standard_only_filter(self):
        races = GameData(str(self.mount)).event_races()
        self.assertEqual([race.id for race in races], [1, 2])
        self.assertEqual((races[0].name, races[0].map_name, races[0].track_id, races[0].route, races[0].laps),
                         ("Base race", "Anthem", 996, 12, 3))
        self.assertEqual(races[1].map_name, "TheIsland")

    def test_active_database_precedence_and_no_writes(self):
        for folder, title in (("mediapc", "PC race"), ("mediaoverride", "Override race")):
            path = self.mount / folder / "stripped" / "gamedbRC.slt"
            self.create_database(path, title)
            before = path.read_bytes()
            self.assertEqual(GameData(str(self.mount)).event_races()[0].name, title)
            self.assertEqual(path.read_bytes(), before)

    def test_localization_uses_track_table_not_car_table(self):
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("UPDATE Tracks SET DisplayName=? WHERE id=996", ("_&%d" % ((0xC992 << 16) | 7),))
            db.commit()
        # 0xc9920007 is the Tracks table reference with string key 7.
        with patch.object(GameData, "_strings", return_value={0xC992: {7: "Localized race"}}):
            self.assertEqual(GameData(str(self.mount)).event_races()[0].name, "Localized race")

    def test_missing_and_invalid_databases_are_reported(self):
        self.path.unlink()
        with self.assertRaises(FileNotFoundError):
            GameData(str(self.mount)).event_races()
        self.path.write_bytes(b"not a database")
        with self.assertRaises(sqlite3.DatabaseError):
            GameData(str(self.mount)).event_races()

    def test_unresolved_track_name_falls_back_to_event_name(self):
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("UPDATE Tracks SET DisplayName=? WHERE id=996", ("_&%d" % ((0xC992 << 16) | 7),))
            db.commit()
        self.assertEqual(GameData(str(self.mount)).event_races()[0].name, "Event")
