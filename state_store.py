#!/usr/bin/env python3
# small atomic JSON file state store
import json
import os
import tempfile

class StateStore:
    def __init__(self, path):
        self.path = path
        self._data = {}
        self._load()

    def _load(self):
        try:
            if os.path.exists(self.path):
                with open(self.path, "r", encoding="utf-8") as f:
                    self._data = json.load(f)
        except Exception:
            self._data = {}

    def get(self, key, default=None):
        return self._data.get(key, default)

    def update(self, d: dict):
        if not isinstance(d, dict):
            return
        self._data.update(d)
        self._atomic_write()

    def _atomic_write(self):
        try:
            parent = os.path.dirname(self.path)
            if parent and not os.path.exists(parent):
                os.makedirs(parent, exist_ok=True)
        except Exception:
            pass
        tmpfd, tmppath = tempfile.mkstemp(dir=os.path.dirname(self.path) or "/tmp")
        try:
            with os.fdopen(tmpfd, "w", encoding="utf-8") as f:
                json.dump(self._data, f)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmppath, self.path)
        except Exception:
            try:
                if os.path.exists(tmppath):
                    os.remove(tmppath)
            except Exception:
                pass
