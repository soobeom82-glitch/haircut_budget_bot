from __future__ import annotations

import json
import threading
from pathlib import Path


class ProcessedUpdateStore:
    def __init__(self, path: Path, max_items: int = 1000) -> None:
        self._path = path
        self._max_items = max_items
        self._lock = threading.Lock()
        self._processed = self._load()

    def has(self, update_id: int) -> bool:
        with self._lock:
            return update_id in self._processed

    def mark(self, update_id: int) -> None:
        with self._lock:
            self._processed.append(update_id)
            self._processed = self._processed[-self._max_items :]
            self._save()

    def _load(self) -> list[int]:
        if not self._path.exists():
            return []
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except OSError:
            return []
        return [int(item) for item in data]

    def _save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(
                json.dumps(self._processed, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError:
            return


def append_ledger_entry(path: Path, entry: dict) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        return
