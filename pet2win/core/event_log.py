"""照翻 Pet2/Core/EventLog.swift：本地事件记录，一行一个 JSON（events.jsonl）。只记事件和数字。只用标准库。"""
import json
import os

from . import paths


class EventLog:
    def __init__(self, directory=None):
        d = directory if directory is not None else paths.data_directory()
        os.makedirs(d, exist_ok=True)
        self.path = os.path.join(d, "events.jsonl")

    @property
    def directory(self):
        return os.path.dirname(self.path)

    def append(self, kind, details, t):
        entry = {"app": "pet2", "details": dict(details), "kind": kind, "t": t}
        try:
            line = json.dumps(entry, sort_keys=True, ensure_ascii=False, separators=(",", ":")) + "\n"
            with open(self.path, "ab") as f:
                f.write(line.encode("utf-8"))
        except (OSError, TypeError, ValueError):
            pass
