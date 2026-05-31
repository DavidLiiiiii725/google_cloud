"""MongoDB client + collection helpers. Falls back to an in-memory store if MONGODB_URI is unset or connection fails — keeps the demo running."""
from __future__ import annotations
import threading
import time
from typing import Any
from pymongo import MongoClient
from pymongo.errors import PyMongoError
from . import config


class _MemoryCollection:
    def __init__(self, name: str):
        self.name = name
        self._docs: list[dict] = []
        self._lock = threading.Lock()
        self._counter = 0

    def insert_one(self, doc: dict):
        with self._lock:
            self._counter += 1
            doc = dict(doc)
            doc.setdefault("_id", f"{self.name}-{self._counter}")
            self._docs.append(doc)
        return type("R", (), {"inserted_id": doc["_id"]})()

    def insert_many(self, docs: list[dict]):
        for d in docs:
            self.insert_one(d)
        return type("R", (), {"inserted_ids": [d.get("_id") for d in docs]})()

    def find(self, query: dict | None = None, sort=None, limit: int | None = None):
        with self._lock:
            results = list(self._docs)
        if query:
            results = [d for d in results if all(d.get(k) == v for k, v in query.items())]
        if sort:
            (key, direction) = sort[0] if isinstance(sort, list) else sort
            results.sort(key=lambda d: d.get(key, 0), reverse=(direction == -1))
        if limit:
            results = results[:limit]
        return results

    def find_one(self, query: dict | None = None, sort=None):
        r = self.find(query, sort=sort, limit=1)
        return r[0] if r else None

    def update_one(self, query: dict, update: dict):
        with self._lock:
            for d in self._docs:
                if all(d.get(k) == v for k, v in query.items()):
                    if "$set" in update:
                        d.update(update["$set"])
                    return type("R", (), {"matched_count": 1, "modified_count": 1})()
        return type("R", (), {"matched_count": 0, "modified_count": 0})()

    def count_documents(self, query: dict | None = None) -> int:
        return len(self.find(query))

    def delete_many(self, query: dict | None = None):
        with self._lock:
            if not query:
                n = len(self._docs)
                self._docs.clear()
                return type("R", (), {"deleted_count": n})()
            keep = [d for d in self._docs if not all(d.get(k) == v for k, v in query.items())]
            n = len(self._docs) - len(keep)
            self._docs = keep
        return type("R", (), {"deleted_count": n})()

    def create_index(self, *_args, **_kwargs):
        return None


class _MemoryDB:
    def __init__(self):
        self._cols: dict[str, _MemoryCollection] = {}
        self._lock = threading.Lock()

    def __getitem__(self, name: str) -> _MemoryCollection:
        with self._lock:
            if name not in self._cols:
                self._cols[name] = _MemoryCollection(name)
            return self._cols[name]


_db = None
_mode = "uninitialized"


def get_db():
    global _db, _mode
    if _db is not None:
        return _db
    if config.MONGODB_URI:
        try:
            client = MongoClient(config.MONGODB_URI, serverSelectionTimeoutMS=4000)
            client.admin.command("ping")
            _db = client[config.MONGODB_DB]
            _mode = "atlas"
            print(f"[db] connected to MongoDB Atlas — db={config.MONGODB_DB}")
            return _db
        except PyMongoError as e:
            print(f"[db] Atlas connect failed: {e}. Falling back to in-memory store.")
    _db = _MemoryDB()
    _mode = "memory"
    print("[db] using in-memory store (no MONGODB_URI or connection failed)")
    return _db


def db_mode() -> str:
    return _mode


# Convenience accessors
def live_telemetry():
    return get_db()["live_telemetry"]


def historical_climate():
    return get_db()["historical_climate"]


def incidents():
    return get_db()["incidents"]


def agent_logs():
    return get_db()["agent_logs"]


def actuators():
    return get_db()["actuators"]
