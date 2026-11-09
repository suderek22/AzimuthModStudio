"""Timed recording and playback for commands entered in the wrapper console."""
import json
import os
import re
import time


MAX_ACTIONS = 10000
MAX_DURATION_MS = 60 * 60 * 1000
MAX_COMMAND_LENGTH = 2048
_SAFE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9 _.-]{0,63}\Z")


class CommandScriptStore:
    def __init__(self, directory=None):
        self.directory = directory or os.path.join("scripts", "commands")

    def path(self, name):
        if not isinstance(name, str) or not _SAFE_NAME.fullmatch(name) or name in (".", ".."):
            raise ValueError("Script name must be 1-64 letters, numbers, spaces, dots, dashes or underscores.")
        return os.path.join(self.directory, name + ".json")

    def names(self):
        try:
            return sorted((entry[:-5] for entry in os.listdir(self.directory)
                           if entry.lower().endswith(".json") and _SAFE_NAME.fullmatch(entry[:-5])),
                          key=str.casefold)
        except FileNotFoundError:
            return []

    def save(self, name, actions):
        path = self.path(name)
        actions = self._validate(actions)
        os.makedirs(self.directory, exist_ok=True)
        temporary = path + ".tmp"
        try:
            with open(temporary, "w", encoding="utf-8", newline="\n") as stream:
                json.dump({"format": "CPS-Commands", "version": 1, "actions": actions},
                          stream, ensure_ascii=False, indent=2)
                stream.write("\n")
            os.replace(temporary, path)
        except OSError:
            try:
                os.remove(temporary)
            except OSError:
                pass
            raise
        return path

    def load(self, name):
        path = self.path(name)
        with open(path, encoding="utf-8") as stream:
            data = json.load(stream)
        if (not isinstance(data, dict) or data.get("format") != "CPS-Commands" or
                data.get("version") != 1 or not isinstance(data.get("actions"), list)):
            raise ValueError("Unsupported or malformed command script.")
        return self._validate(data["actions"])

    @staticmethod
    def _validate(actions):
        if not isinstance(actions, list):
            raise ValueError("Command script actions must be a list.")
        if len(actions) > MAX_ACTIONS:
            raise ValueError("Command script exceeds the %d-action limit." % MAX_ACTIONS)
        validated = []
        previous = -1
        for action in actions:
            if not isinstance(action, dict):
                raise ValueError("Command script contains an invalid action.")
            at_ms, command = action.get("at_ms"), action.get("command")
            if (not isinstance(at_ms, int) or isinstance(at_ms, bool) or at_ms < 0 or
                    at_ms < previous or at_ms > MAX_DURATION_MS):
                raise ValueError("Command script timestamps must be ordered within one hour.")
            if (not isinstance(command, str) or not command.strip() or
                    len(command) > MAX_COMMAND_LENGTH or "\n" in command or "\r" in command or
                    command.strip().split(None, 1)[0].lower() == "script"):
                raise ValueError("Command script contains an invalid or recursive command.")
            validated.append({"at_ms": at_ms, "command": command.strip()})
            previous = at_ms
        return validated


class CommandScriptRecorder:
    def __init__(self, store):
        self.store = store
        self.name = None
        self.started_at = None
        self.actions = []

    @property
    def recording(self):
        return self.started_at is not None

    def start(self, name):
        self.store.path(name)
        self.name = name
        self.started_at = time.monotonic()
        self.actions = []

    def add(self, command):
        if not self.recording:
            return False
        command = command.strip()
        if not command or command.split(None, 1)[0].lower() == "script":
            return False
        if len(command) > MAX_COMMAND_LENGTH or "\n" in command or "\r" in command:
            raise ValueError("Command is too long or contains a line break.")
        if len(self.actions) >= MAX_ACTIONS:
            raise ValueError("Command script reached the %d-action limit; stop recording." % MAX_ACTIONS)
        at_ms = min(int((time.monotonic() - self.started_at) * 1000), MAX_DURATION_MS)
        if at_ms >= MAX_DURATION_MS:
            raise ValueError("Command script reached the one-hour duration limit; stop recording.")
        self.actions.append({"at_ms": at_ms, "command": command})
        return True

    def stop(self):
        if not self.recording:
            return None
        name, actions = self.name, self.actions
        path = self.store.save(name, actions)
        self.name = None
        self.started_at = None
        self.actions = []
        return path
