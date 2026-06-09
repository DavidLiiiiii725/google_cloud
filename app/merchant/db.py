"""MongoDB client + collection helpers for the Merchant Agent.

Structurally identical to app/transport/db.py — SAME connection, SAME in-memory
fallback (_MemoryDB / _MemoryCollection) so offline demos work — with the Merchant's
collection accessors added at the bottom. Keeping the file identical in structure means
all three agents behave the same way when the database is unreachable.

The Merchant READS `farms` + `transport_plans` + `world_events` (written by the other
two agents) and WRITES `market_orders` + `buyers` (its own). No agent calls another in
process; coordination happens purely through these shared documents.
"""
from __future__ import annotations
import threading
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

    def update_one(self, query: dict, update: dict, upsert: bool = False):
        with self._lock:
            for d in self._docs:
                if all(d.get(k) == v for k, v in query.items()):
                    if "$set" in update:
                        d.update(update["$set"])
                    return type("R", (), {"matched_count": 1, "modified_count": 1})()
            if upsert:
                new = dict(query)
                if "$set" in update:
                    new.update(update["$set"])
                self._counter += 1
                new.setdefault("_id", f"{self.name}-{self._counter}")
                self._docs.append(new)
                return type("R", (), {"matched_count": 0, "modified_count": 0, "upserted_id": new["_id"]})()
        return type("R", (), {"matched_count": 0, "modified_count": 0})()

    def replace_one(self, query: dict, doc: dict, upsert: bool = False):
        with self._lock:
            for i, d in enumerate(self._docs):
                if all(d.get(k) == v for k, v in query.items()):
                    self._docs[i] = dict(doc)
                    return type("R", (), {"matched_count": 1, "modified_count": 1})()
            if upsert:
                self._docs.append(dict(doc))
                return type("R", (), {"matched_count": 0, "modified_count": 0, "upserted_id": doc.get("_id")})()
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


class _ListFindCollection:
    """Wraps a real pymongo Collection so ``find()`` returns a list instead of a
    Cursor — matching the in-memory store the codebase was written against."""
    def __init__(self, col):
        self._col = col

    def find(self, *args, **kwargs):
        return list(self._col.find(*args, **kwargs))

    def __getattr__(self, name):
        return getattr(self._col, name)


class _ListFindDB:
    def __init__(self, database):
        self._database = database

    def __getitem__(self, name):
        return _ListFindCollection(self._database[name])


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
            _db = _ListFindDB(client[config.MONGODB_DB])
            _mode = "mongodb"
            print(f"[db] merchant connected to MongoDB — db={config.MONGODB_DB}")
            return _db
        except PyMongoError as e:
            print(f"[db] merchant connect failed: {e}. Falling back to in-memory store.")
    _db = _MemoryDB()
    _mode = "memory"
    print("[db] merchant using in-memory store (no MONGODB_URI or connection failed)")
    return _db


def db_mode() -> str:
    return _mode


# ---- Blackboard collections ------------------------------------------------
# Written by other agents (read-only for the Merchant):
def farms():
    return get_db()["farms"]


def transport_plans():
    return get_db()["transport_plans"]


def world_events():
    return get_db()["world_events"]


# Owned by the Merchant:
def market_orders():
    return get_db()["market_orders"]


def buyers():
    return get_db()["buyers"]


def agent_logs():
    return get_db()["agent_logs"]
