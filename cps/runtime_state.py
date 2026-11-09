"""Validated snapshots of the native Studio command replies."""
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class TrackState:
    current: int
    settled: bool
    queued: int
    target: int
    stage: str
    timed_out: bool

    @property
    def idle(self) -> bool:
        return self.settled and self.queued == -1 and self.target == -1


def parse_track_state(reply: str) -> TrackState:
    match = re.fullmatch(
        r"track current=(-?\d+) settled=([01]) queued=(-?\d+) target=(-?\d+) "
        r"stage=(.*?) elapsed_ms=\d+ timed_out=([01])", reply.strip())
    if not match:
        raise ValueError("Cannot read map state: " + reply)
    return TrackState(int(match[1]), match[2] == "1", int(match[3]), int(match[4]),
                      match[5], match[6] == "1")


@dataclass(frozen=True)
class SpawnedObject:
    id: int
    kind: str
    car_id: int
    state: int
    removing: bool
    located: bool
    ai: str
    possessed: bool

    @property
    def can_swap(self) -> bool:
        return self.ai == "none" and self.state == 2 and self.located and not self.removing and not self.possessed

    @property
    def label(self) -> str:
        if self.removing:
            return "Removing"
        if self.possessed:
            return "Player controlled"
        if self.state != 2 or not self.located:
            return "Loading (state %d)" % self.state
        return "Active"


def parse_spawned(reply: str) -> list[SpawnedObject]:
    lines = reply.strip().splitlines()
    header = re.fullmatch(r"Studio spawned cars: (\d+)/\d+, page 1/1 \(use object ID for removal\):",
                          lines[0] if lines else "")
    if not header:
        raise ValueError("Cannot read spawned objects: " + reply)
    objects = []
    for line in lines[1:]:
        match = re.fullmatch(r"(car) (\d+) (.+)", line)
        if not match:
            raise ValueError("Invalid spawned object: " + line)
        fields = dict(part.split("=", 1) for part in match[3].split() if "=" in part)
        try:
            ai = fields["ai"]
            if ai not in ("none", "paused", "playing"):
                raise ValueError("Unknown AI mode: " + ai)
            objects.append(SpawnedObject(int(match[2]), match[1], int(fields["carID"]), int(fields["state"]),
                                         bool(int(fields["removing"])), bool(int(fields["located"])),
                                         ai, bool(int(fields["possessed"]))))
        except (KeyError, ValueError) as ex:
            raise ValueError("Invalid spawned object: " + line) from ex
    if len(objects) != int(header[1]) or len({obj.id for obj in objects}) != len(objects):
        raise ValueError("Incomplete or duplicate spawned object list: " + reply)
    return objects
