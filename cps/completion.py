"""Console command candidates; completion never sends commands to the game."""
from .travel import MAP_DESTINATIONS, load_places

COMMANDS = (
    "help", "list", "status", "echo ", "time ", "freeze 0", "freeze 1",
    "weather off", "weather ", "recover 0", "recover 1",
    "car ", "car status", "car details", "car recover", "car force",
    "volume 0", "volume 0.1", "volume 0.4", "volume 1",
    "console 0", "console 1", "filter text ", "filter -text ", "filter resource ",
    "filter -resource ", "filter remove", "debug Azimuth", "debug off",
    "track Anthem", "track TheIsland", "track list", "track status",
    "tp list", "tp status", "tp add ", "tp remove ",
    "event list", "event info ", "event start ", "event stop", "event status",
    "spawn car ", "spawn ai ", "spawn drivatar ", "spawn clear", "spawn list", "spawn status",
    "spawn remove car ", "spawn swap ", "spawn swap reset", "spawn swap status",
    "drivatar pause", "drivatar play", "drivatar reset", "drivatar swap reset",
    "trace start", "trace stop", "trace status",
    "input record 0", "input record 1", "input record 2", "input record 3",
    "input play", "input stop", "input status",
    "animations", "animation ",
    "pipelines on", "pipelines off", "pipelines status",
)


def command_candidates():
    entries = list(COMMANDS)
    entries.extend('tp %s "%s"' % (name, place) for name, places in MAP_DESTINATIONS.items() for place in places)
    for place in load_places():
        entries.extend(('tp %d "%s"' % (place.track_id, place.name),
                        'tp "%s"' % place.name,
                        'tp remove "%s"' % place.name))
    return entries
