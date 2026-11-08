"""HoloCore-native, local episodic memory storage."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping


SCHEMA_VERSION = 4


_BASE_SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_info(version INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS worlds(
    id TEXT PRIMARY KEY, display_name TEXT NOT NULL, metadata TEXT NOT NULL,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sectors(
    world_id TEXT NOT NULL REFERENCES worlds(id) ON DELETE CASCADE,
    id TEXT NOT NULL, display_name TEXT NOT NULL, metadata TEXT NOT NULL,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
    PRIMARY KEY(world_id, id));
CREATE TABLE IF NOT EXISTS shards(
    id TEXT PRIMARY KEY, world_id TEXT NOT NULL REFERENCES worlds(id) ON DELETE CASCADE,
    sector_id TEXT, content TEXT NOT NULL, content_hash TEXT NOT NULL,
    route_hint TEXT, transformations TEXT NOT NULL, privacy TEXT NOT NULL,
    metadata TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
    FOREIGN KEY(world_id, sector_id) REFERENCES sectors(world_id, id));
CREATE UNIQUE INDEX IF NOT EXISTS uq_shard_content
    ON shards(world_id, ifnull(sector_id, ''), content_hash);
CREATE INDEX IF NOT EXISTS ix_shard_scope ON shards(world_id, sector_id);
CREATE TABLE IF NOT EXISTS provenance(
    id INTEGER PRIMARY KEY, shard_id TEXT NOT NULL REFERENCES shards(id) ON DELETE CASCADE,
    world_id TEXT NOT NULL REFERENCES worlds(id) ON DELETE CASCADE,
    source_ref TEXT NOT NULL, chunk_index INTEGER NOT NULL, version_token TEXT,
    metadata TEXT NOT NULL, first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL,
    UNIQUE(world_id, source_ref, chunk_index));
CREATE INDEX IF NOT EXISTS ix_provenance_shard ON provenance(shard_id);
CREATE TABLE IF NOT EXISTS checkpoints(
    world_id TEXT NOT NULL REFERENCES worlds(id) ON DELETE CASCADE,
    sector_id TEXT NOT NULL DEFAULT '', mode TEXT NOT NULL, source_ref TEXT NOT NULL,
    cursor TEXT NOT NULL, updated_at TEXT NOT NULL,
    PRIMARY KEY(world_id, sector_id, mode, source_ref));
"""

_MIGRATIONS: dict[int, tuple[str, str]] = {
    2: (
        "episodic diary",
        """
        CREATE TABLE IF NOT EXISTS diary(
            id TEXT PRIMARY KEY, world_id TEXT NOT NULL REFERENCES worlds(id) ON DELETE CASCADE,
            sector_id TEXT NOT NULL DEFAULT '', occurred_at TEXT NOT NULL, title TEXT NOT NULL,
            content TEXT NOT NULL, kind TEXT NOT NULL, metadata TEXT NOT NULL,
            created_at TEXT NOT NULL, UNIQUE(world_id, sector_id, content));
        CREATE TABLE IF NOT EXISTS diary_provenance(
            id INTEGER PRIMARY KEY, diary_id TEXT NOT NULL REFERENCES diary(id) ON DELETE CASCADE,
            source_ref TEXT NOT NULL, chunk_index INTEGER NOT NULL, version_token TEXT,
            metadata TEXT NOT NULL, first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL,
            UNIQUE(diary_id, source_ref, chunk_index));
        """,
    ),
    3: (
        "memory groups and tracked history",
        """
        CREATE TABLE IF NOT EXISTS rooms(
            world_id TEXT NOT NULL REFERENCES worlds(id) ON DELETE CASCADE,
            id TEXT NOT NULL, name TEXT NOT NULL, description TEXT NOT NULL,
            metadata TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            PRIMARY KEY(world_id, id), UNIQUE(world_id, name));
        CREATE TABLE IF NOT EXISTS entities(
            id TEXT PRIMARY KEY, world_id TEXT NOT NULL REFERENCES worlds(id) ON DELETE CASCADE,
            room_id TEXT, name TEXT NOT NULL, kind TEXT NOT NULL, metadata TEXT NOT NULL,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            FOREIGN KEY(world_id, room_id) REFERENCES rooms(world_id, id) ON DELETE SET NULL,
            UNIQUE(world_id, name, kind));
        CREATE INDEX IF NOT EXISTS ix_entities_scope ON entities(world_id, room_id);
        CREATE TABLE IF NOT EXISTS entity_events(
            id TEXT PRIMARY KEY, entity_id TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
            world_id TEXT NOT NULL REFERENCES worlds(id) ON DELETE CASCADE,
            relation TEXT NOT NULL, value TEXT NOT NULL, occurred_at TEXT NOT NULL,
            valid_from TEXT, valid_to TEXT, source_ref TEXT NOT NULL, metadata TEXT NOT NULL,
            UNIQUE(entity_id, relation, value, source_ref));
        CREATE INDEX IF NOT EXISTS ix_entity_events_scope ON entity_events(world_id, occurred_at);
        """,
    ),
    4: (
        "migration journal",
        """
        CREATE TABLE IF NOT EXISTS schema_migrations(
            version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at TEXT NOT NULL);
        """,
    ),
}

_SHARED_ACTIVITY_TABLES = {
    "agent_runs", "activity_actions", "activity_items", "action_items", "agent_handoffs",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _required(value: str, label: str) -> str:
    value = str(value).strip()
    if not value or "\x00" in value:
        raise ValueError(f"{label} must be a non-empty string without NUL characters")
    return value


def _json(value: Mapping[str, Any] | None) -> str:
    return json.dumps(dict(value or {}), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


@dataclass(frozen=True)
class World:
    id: str
    display_name: str
    metadata: Mapping[str, Any] = field(default_factory=dict)
    created_at: str = ""
    updated_at: str = ""


@dataclass(frozen=True)
class Sector:
    world_id: str
    id: str
    display_name: str
    metadata: Mapping[str, Any] = field(default_factory=dict)
    created_at: str = ""
    updated_at: str = ""


@dataclass(frozen=True)
class SourceProvenance:
    source_ref: str
    chunk_index: int
    version_token: str | None
    metadata: Mapping[str, Any]
    first_seen_at: str
    last_seen_at: str


@dataclass(frozen=True)
class MemoryShard:
    id: str
    world_id: str
    sector_id: str | None
    content: str
    content_hash: str
    route_hint: str | None
    transformations: tuple[str, ...]
    privacy: Mapping[str, Any]
    metadata: Mapping[str, Any]
    provenance: tuple[SourceProvenance, ...]
    created_at: str
    updated_at: str
    score: int = 0
    action: str = "stored"
    deduplicated: bool = False


@dataclass(frozen=True)
class ShardInput:
    content: str
    source_ref: str
    sector: str | None = None
    version_token: str | None = None
    chunk_index: int = 0
    route_hint: str | None = None
    transformations: tuple[str, ...] = ()
    privacy: Mapping[str, Any] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)
    source_metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SyncReport:
    world_id: str
    sector_id: str | None
    inserted: int
    updated: int
    unchanged: int
    deduplicated: int
    removed_sources: int
    removed_shards: int


@dataclass(frozen=True)
class DiaryRecord:
    id: str
    world_id: str
    sector_id: str | None
    occurred_at: str
    title: str
    content: str
    kind: str
    metadata: Mapping[str, Any]
    provenance: tuple[SourceProvenance, ...]


@dataclass(frozen=True)
class Checkpoint:
    world_id: str
    sector_id: str | None
    mode: str
    source_ref: str
    cursor: str
    updated_at: str


@dataclass(frozen=True)
class Room:
    world_id: str
    id: str
    name: str
    description: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Entity:
    id: str
    world_id: str
    room_id: str | None
    name: str
    kind: str
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class EntityEvent:
    id: str
    entity_id: str
    world_id: str
    relation: str
    value: str
    occurred_at: str
    valid_from: str | None
    valid_to: str | None
    source_ref: str
    metadata: Mapping[str, Any] = field(default_factory=dict)


class MemoryStore:
    """SQLite-backed project Memories, groups, provenance, and tracked history."""

    def __init__(self, root: str | Path):
        root = Path(root)
        self.path = root if root.suffix in {".db", ".sqlite", ".sqlite3"} else root / "animus.sqlite3"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.migration_report: dict[str, Any] = {
            "from_version": None, "to_version": SCHEMA_VERSION,
            "backup": None, "applied": [],
        }
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    @contextmanager
    def _connection(self):
        db = self._connect()
        try:
            with db:
                yield db
        finally:
            db.close()

    def _initialize(self) -> None:
        database_existed = self.path.is_file() and self.path.stat().st_size > 0
        db = self._connect()
        try:
            db.execute("PRAGMA journal_mode = WAL")
            has_schema_info = db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_info'"
            ).fetchone() is not None
            existing_tables = {
                str(row[0]) for row in db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                ).fetchall()
            }
            if database_existed and not has_schema_info and not existing_tables <= _SHARED_ACTIVITY_TABLES:
                unknown = ", ".join(sorted(existing_tables - _SHARED_ACTIVITY_TABLES)) or "unknown tables"
                raise RuntimeError(f"unsupported Memories database without schema_info: {unknown}")
            row = db.execute("SELECT version FROM schema_info LIMIT 1").fetchone() if has_schema_info else None
            from_version = int(row[0]) if row is not None else 0
            self.migration_report["from_version"] = from_version
            if from_version > SCHEMA_VERSION:
                raise RuntimeError(f"unsupported Memories schema version: {from_version}")
            if from_version < SCHEMA_VERSION and existing_tables:
                self._assert_integrity(db, "before migration")
                db.commit()
                self.migration_report["backup"] = str(self._backup_database(db, from_version))

            db.execute("BEGIN IMMEDIATE")
            if from_version == 0:
                self._execute_statements(db, _BASE_SCHEMA)
                db.execute("DELETE FROM schema_info")
                db.execute("INSERT INTO schema_info VALUES (1)")
                from_version = 1
            else:
                # Idempotently repair indexes from the stable v1 base contract.
                self._execute_statements(db, _BASE_SCHEMA)
            db.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations("
                "version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at TEXT NOT NULL)"
            )
            if not self.migration_report["applied"] and self.migration_report["from_version"] == 0:
                db.execute(
                    "INSERT OR IGNORE INTO schema_migrations VALUES (?,?,?)",
                    (1, "base memories schema", _now()),
                )
            for target in range(from_version + 1, SCHEMA_VERSION + 1):
                name, script = _MIGRATIONS[target]
                self._execute_statements(db, script)
                db.execute("UPDATE schema_info SET version=?", (target,))
                db.execute(
                    "INSERT OR REPLACE INTO schema_migrations VALUES (?,?,?)",
                    (target, name, _now()),
                )
                self.migration_report["applied"].append({"version": target, "name": name})
            self._assert_integrity(db, "after migration")
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def _execute_statements(db: sqlite3.Connection, script: str) -> None:
        for statement in script.split(";"):
            if statement.strip():
                db.execute(statement)

    @staticmethod
    def _assert_integrity(db: sqlite3.Connection, stage: str) -> None:
        result = [str(row[0]) for row in db.execute("PRAGMA quick_check").fetchall()]
        if result != ["ok"]:
            raise RuntimeError(f"Memories integrity check failed {stage}: {'; '.join(result)}")

    def _backup_database(self, db: sqlite3.Connection, from_version: int) -> Path:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup = self.path.with_name(
            f"{self.path.name}.pre-v{SCHEMA_VERSION}-from-v{from_version}-{stamp}.bak"
        )
        with sqlite3.connect(backup) as target:
            db.backup(target)
        return backup

    def close(self) -> None:
        """Connections are operation-scoped; provided for lifecycle compatibility."""

    def __enter__(self) -> "MemoryStore":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def create_world(self, world_id: str, display_name: str | None = None,
                     metadata: Mapping[str, Any] | None = None) -> World:
        world_id = _required(world_id, "world_id")
        name, now = display_name or world_id, _now()
        with self._connection() as db:
            db.execute(
                "INSERT INTO worlds VALUES(?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
                "display_name=excluded.display_name, metadata=excluded.metadata, updated_at=excluded.updated_at",
                (world_id, name, _json(metadata), now, now),
            )
        return self.get_world(world_id)

    def rename_world(
        self, old_id: str, new_id: str, display_name: str | None = None
    ) -> bool:
        """Rename a World and every scoped record in one SQLite transaction."""
        old_id = _required(old_id, "old_id")
        new_id = _required(new_id, "new_id")
        if old_id == new_id:
            return False
        db = self._connect()
        try:
            if db.execute("SELECT 1 FROM worlds WHERE id=?", (old_id,)).fetchone() is None:
                return False
            target_exists = db.execute(
                "SELECT 1 FROM worlds WHERE id=?", (new_id,)
            ).fetchone() is not None
            if target_exists:
                substantive = sum(
                    int(db.execute(f"SELECT count(*) FROM {table} WHERE world_id=?", (old_id,)).fetchone()[0])
                    for table in ("shards", "provenance", "checkpoints", "diary", "rooms", "entities", "entity_events")
                )
                if substantive:
                    raise ValueError(
                        f"cannot merge Memories project {old_id!r} into existing {new_id!r}: "
                        f"the old scope contains {substantive} records"
                    )
            db.commit()
            db.execute("PRAGMA foreign_keys = OFF")
            db.execute("BEGIN IMMEDIATE")
            if target_exists:
                db.execute(
                    "INSERT OR IGNORE INTO sectors "
                    "SELECT ?, id, display_name, metadata, created_at, updated_at "
                    "FROM sectors WHERE world_id=?",
                    (new_id, old_id),
                )
                db.execute("DELETE FROM sectors WHERE world_id=?", (old_id,))
                db.execute("DELETE FROM worlds WHERE id=?", (old_id,))
                db.execute(
                    "UPDATE worlds SET display_name=?, updated_at=? WHERE id=?",
                    (display_name or new_id, _now(), new_id),
                )
            else:
                for table in ("sectors", "shards", "provenance", "checkpoints", "diary", "rooms", "entities", "entity_events"):
                    db.execute(f"UPDATE {table} SET world_id=? WHERE world_id=?", (new_id, old_id))
                db.execute(
                    "UPDATE worlds SET id=?, display_name=?, updated_at=? WHERE id=?",
                    (new_id, display_name or new_id, _now(), old_id),
                )
            db.commit()
            db.execute("PRAGMA foreign_keys = ON")
            violations = db.execute("PRAGMA foreign_key_check").fetchall()
            if violations:
                raise RuntimeError(f"Memories project rename left foreign-key violations: {violations}")
            return True
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def merge_database(self, source: str | Path, *, world: str | None = None) -> dict[str, int]:
        """Merge a legacy Memories database into this store without deleting it."""
        source_path = Path(source).expanduser().resolve()
        if source_path == self.path.resolve():
            return {"worlds": 0, "shards": 0, "provenance": 0, "diary": 0}
        source_db = sqlite3.connect(f"file:{source_path}?mode=ro", uri=True)
        source_db.row_factory = sqlite3.Row
        try:
            source_worlds = source_db.execute("SELECT * FROM worlds").fetchall()
            override = _required(world, "world") if world else None
            world_map = {row["id"]: (override or row["id"]) for row in source_worlds}
            counts = {"worlds": 0, "shards": 0, "provenance": 0, "diary": 0}
            shard_map: dict[str, str] = {}
            diary_map: dict[str, str] = {}
            with self._connection() as db:
                for row in source_worlds:
                    target_world = world_map[row["id"]]
                    before = db.total_changes
                    db.execute(
                        "INSERT OR IGNORE INTO worlds VALUES(?,?,?,?,?)",
                        (target_world, row["display_name"], row["metadata"], row["created_at"], row["updated_at"]),
                    )
                    counts["worlds"] += int(db.total_changes > before)
                for row in source_db.execute("SELECT * FROM sectors"):
                    db.execute(
                        "INSERT OR IGNORE INTO sectors VALUES(?,?,?,?,?,?)",
                        (world_map[row["world_id"]], row["id"], row["display_name"], row["metadata"], row["created_at"], row["updated_at"]),
                    )
                for row in source_db.execute("SELECT * FROM shards"):
                    target_world = world_map[row["world_id"]]
                    values = (
                        row["id"], target_world, row["sector_id"], row["content"], row["content_hash"],
                        row["route_hint"], row["transformations"], row["privacy"], row["metadata"],
                        row["created_at"], row["updated_at"],
                    )
                    before = db.total_changes
                    db.execute("INSERT OR IGNORE INTO shards VALUES(?,?,?,?,?,?,?,?,?,?,?)", values)
                    if db.total_changes > before:
                        shard_map[row["id"]] = row["id"]
                        counts["shards"] += 1
                    else:
                        existing = db.execute(
                            "SELECT id FROM shards WHERE world_id=? AND ifnull(sector_id,'')=ifnull(?,'') AND content_hash=?",
                            (target_world, row["sector_id"], row["content_hash"]),
                        ).fetchone()
                        shard_map[row["id"]] = existing["id"] if existing else row["id"]
                for row in source_db.execute("SELECT * FROM provenance"):
                    before = db.total_changes
                    db.execute(
                        "INSERT OR IGNORE INTO provenance(shard_id,world_id,source_ref,chunk_index,version_token,metadata,first_seen_at,last_seen_at) VALUES(?,?,?,?,?,?,?,?)",
                        (shard_map[row["shard_id"]], world_map[row["world_id"]], row["source_ref"], row["chunk_index"], row["version_token"], row["metadata"], row["first_seen_at"], row["last_seen_at"]),
                    )
                    counts["provenance"] += int(db.total_changes > before)
                for row in source_db.execute("SELECT * FROM checkpoints"):
                    db.execute(
                        "INSERT OR REPLACE INTO checkpoints VALUES(?,?,?,?,?,?)",
                        (world_map[row["world_id"]], row["sector_id"], row["mode"], row["source_ref"], row["cursor"], row["updated_at"]),
                    )
                for row in source_db.execute("SELECT * FROM diary"):
                    target_world = world_map[row["world_id"]]
                    before = db.total_changes
                    db.execute(
                        "INSERT OR IGNORE INTO diary VALUES(?,?,?,?,?,?,?,?,?)",
                        (row["id"], target_world, row["sector_id"], row["occurred_at"], row["title"], row["content"], row["kind"], row["metadata"], row["created_at"]),
                    )
                    counts["diary"] += int(db.total_changes > before)
                    existing = db.execute(
                        "SELECT id FROM diary WHERE world_id=? AND sector_id=? AND content=?",
                        (target_world, row["sector_id"], row["content"]),
                    ).fetchone()
                    diary_map[row["id"]] = existing["id"] if existing else row["id"]
                for row in source_db.execute("SELECT * FROM diary_provenance"):
                    db.execute(
                        "INSERT OR IGNORE INTO diary_provenance(diary_id,source_ref,chunk_index,version_token,metadata,first_seen_at,last_seen_at) VALUES(?,?,?,?,?,?,?)",
                        (diary_map[row["diary_id"]], row["source_ref"], row["chunk_index"], row["version_token"], row["metadata"], row["first_seen_at"], row["last_seen_at"]),
                    )
            return counts
        finally:
            source_db.close()

    def get_world(self, world_id: str) -> World:
        with self._connection() as db:
            row = db.execute("SELECT * FROM worlds WHERE id=?", (world_id,)).fetchone()
        if row is None:
            raise KeyError(f"unknown World: {world_id}")
        return World(row["id"], row["display_name"], json.loads(row["metadata"]), row["created_at"], row["updated_at"])

    def create_sector(self, world: str, sector_id: str, display_name: str | None = None,
                      metadata: Mapping[str, Any] | None = None) -> Sector:
        self.get_world(world)
        sector_id, now = _required(sector_id, "sector_id"), _now()
        with self._connection() as db:
            db.execute(
                "INSERT INTO sectors VALUES(?,?,?,?,?,?) ON CONFLICT(world_id,id) DO UPDATE SET "
                "display_name=excluded.display_name, metadata=excluded.metadata, updated_at=excluded.updated_at",
                (world, sector_id, display_name or sector_id, _json(metadata), now, now),
            )
        return self.get_sector(world, sector_id)

    def get_sector(self, world: str, sector_id: str) -> Sector:
        with self._connection() as db:
            row = db.execute("SELECT * FROM sectors WHERE world_id=? AND id=?", (world, sector_id)).fetchone()
        if row is None:
            raise KeyError(f"unknown Sector: {world}/{sector_id}")
        return Sector(row["world_id"], row["id"], row["display_name"], json.loads(row["metadata"]), row["created_at"], row["updated_at"])

    # MemPalace wings/rooms/drawers become World/Room/Entity here. Entities
    # keep temporal assertions so a later value does not erase history.
    def create_room(self, world: str, name: str, description: str = "", metadata: Mapping[str, Any] | None = None) -> Room:
        self.get_world(world); name = _required(name, "room name"); now = _now()
        room_id = re.sub(r"[^a-z0-9]+", "-", name.casefold()).strip("-") or "room"
        with self._connection() as db:
            db.execute("INSERT INTO rooms VALUES(?,?,?,?,?,?,?) ON CONFLICT(world_id,id) DO UPDATE SET name=excluded.name,description=excluded.description,metadata=excluded.metadata,updated_at=excluded.updated_at", (world, room_id, name, description, _json(metadata), now, now))
        return Room(world, room_id, name, description, dict(metadata or {}))

    def rooms(self, *, world: str) -> list[Room]:
        self.get_world(world)
        with self._connection() as db: rows = db.execute("SELECT * FROM rooms WHERE world_id=? ORDER BY name", (world,)).fetchall()
        return [Room(r["world_id"], r["id"], r["name"], r["description"], json.loads(r["metadata"])) for r in rows]

    def upsert_entity(self, name: str, *, world: str, kind: str = "concept", room: str | None = None, metadata: Mapping[str, Any] | None = None) -> Entity:
        self.get_world(world); name, kind, now = _required(name, "entity name"), _required(kind, "entity kind"), _now(); room_id = self.create_room(world, room).id if room else None
        entity_id = hashlib.sha256(f"{world}\0{kind}\0{name.casefold()}".encode()).hexdigest()[:32]
        with self._connection() as db:
            db.execute("INSERT INTO entities VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(world_id,name,kind) DO UPDATE SET room_id=excluded.room_id,metadata=excluded.metadata,updated_at=excluded.updated_at", (entity_id, world, room_id, name, kind, _json(metadata), now, now))
            row = db.execute("SELECT * FROM entities WHERE world_id=? AND name=? AND kind=?", (world, name, kind)).fetchone()
        return Entity(row["id"], row["world_id"], row["room_id"], row["name"], row["kind"], json.loads(row["metadata"]))

    def record_entity_event(self, entity: str, relation: str, value: str, *, world: str, kind: str = "concept", room: str | None = None, occurred_at: str | None = None, valid_from: str | None = None, valid_to: str | None = None, source_ref: str = "memory", metadata: Mapping[str, Any] | None = None) -> EntityEvent:
        item = self.upsert_entity(entity, world=world, kind=kind, room=room); occurred_at = occurred_at or _now(); event_id = hashlib.sha256(f"{item.id}\0{relation}\0{value}\0{source_ref}".encode()).hexdigest()[:32]
        with self._connection() as db:
            db.execute("INSERT INTO entity_events VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(entity_id,relation,value,source_ref) DO UPDATE SET valid_to=excluded.valid_to,metadata=excluded.metadata", (event_id, item.id, world, _required(relation, "relation"), _required(value, "value"), occurred_at, valid_from, valid_to, source_ref, _json(metadata)))
            row = db.execute("SELECT * FROM entity_events WHERE id=?", (event_id,)).fetchone()
        return EntityEvent(row["id"], row["entity_id"], row["world_id"], row["relation"], row["value"], row["occurred_at"], row["valid_from"], row["valid_to"], row["source_ref"], json.loads(row["metadata"]))

    def entity_timeline(self, *, world: str, entity: str | None = None, room: str | None = None, limit: int = 100) -> list[EntityEvent]:
        self.get_world(world); sql = "SELECT ev.* FROM entity_events ev JOIN entities e ON e.id=ev.entity_id WHERE ev.world_id=?"; params: list[Any] = [world]
        if entity: sql += " AND e.name=?"; params.append(entity)
        if room: sql += " AND e.room_id=?"; params.append(room)
        sql += " ORDER BY ev.occurred_at DESC LIMIT ?"; params.append(limit)
        with self._connection() as db: rows = db.execute(sql, params).fetchall()
        return [EntityEvent(r["id"], r["entity_id"], r["world_id"], r["relation"], r["value"], r["occurred_at"], r["valid_from"], r["valid_to"], r["source_ref"], json.loads(r["metadata"])) for r in rows]

    def entity_graph(self, *, world: str, room: str | None = None) -> dict[str, Any]:
        events = self.entity_timeline(world=world, room=room, limit=10000)
        return {"world": world, "events": [event.__dict__ for event in events]}

    # HoloCore public vocabulary. The older room/entity method names remain
    # as compatibility aliases, but user-facing surfaces use Deck/Signal/
    # Chronicle/Constellation language.
    create_deck = create_room
    decks = rooms
    upsert_signal = upsert_entity
    record_signal_chronicle = record_entity_event
    signal_chronicle = entity_timeline
    signal_constellation = entity_graph

    def ingest(self, content: str, *, world: str, source_ref: str, sector: str | None = None,
               version_token: str | None = None, chunk_index: int = 0,
               route_hint: str | None = None, transformations: Iterable[str] = (),
               privacy: Mapping[str, Any] | None = None, metadata: Mapping[str, Any] | None = None,
               source_metadata: Mapping[str, Any] | None = None) -> MemoryShard:
        self.get_world(world)
        if sector is not None:
            self.get_sector(world, sector)
        content, source_ref = _required(content, "content"), _required(source_ref, "source_ref")
        if not isinstance(chunk_index, int) or chunk_index < 0:
            raise ValueError("chunk_index must be a non-negative integer")
        with self._connection() as db:
            shard_id, action, dedup = self._ingest(db, ShardInput(
                content, source_ref, sector, version_token, chunk_index, route_hint,
                tuple(transformations), privacy or {}, metadata or {}, source_metadata or {}), world)
        return self._get_shard(shard_id, action=action, deduplicated=dedup)

    mine = ingest
    update = ingest

    def _ingest(self, db: sqlite3.Connection, item: ShardInput, world: str) -> tuple[str, str, bool]:
        digest = hashlib.sha256(item.content.encode("utf-8")).hexdigest()
        existing_source = db.execute(
            "SELECT p.shard_id,s.content_hash FROM provenance p JOIN shards s ON s.id=p.shard_id "
            "WHERE p.world_id=? AND p.source_ref=? AND p.chunk_index=?",
            (world, item.source_ref, item.chunk_index),).fetchone()
        target = db.execute(
            "SELECT id FROM shards WHERE world_id=? AND sector_id IS ? AND content_hash=?",
            (world, item.sector, digest),).fetchone()
        now = _now()
        dedup = target is not None and (existing_source is None or target["id"] != existing_source["shard_id"])
        if target is None:
            shard_id = hashlib.sha256(f"{world}\0{item.sector or ''}\0{digest}".encode()).hexdigest()[:32]
            db.execute("INSERT INTO shards VALUES(?,?,?,?,?,?,?,?,?,?,?)", (
                shard_id, world, item.sector, item.content, digest, item.route_hint,
                json.dumps(list(item.transformations), ensure_ascii=False), _json(item.privacy),
                _json(item.metadata), now, now))
        else:
            shard_id = target["id"]
        if existing_source is None:
            db.execute("INSERT INTO provenance(shard_id,world_id,source_ref,chunk_index,version_token,metadata,first_seen_at,last_seen_at) VALUES(?,?,?,?,?,?,?,?)",
                       (shard_id, world, item.source_ref, item.chunk_index, item.version_token, _json(item.source_metadata), now, now))
            action = "deduplicated" if dedup else "inserted"
        elif existing_source["shard_id"] == shard_id:
            db.execute("UPDATE provenance SET version_token=?,metadata=?,last_seen_at=? WHERE world_id=? AND source_ref=? AND chunk_index=?",
                       (item.version_token, _json(item.source_metadata), now, world, item.source_ref, item.chunk_index))
            action = "unchanged"
        else:
            old_id = existing_source["shard_id"]
            db.execute("UPDATE provenance SET shard_id=?,version_token=?,metadata=?,last_seen_at=? WHERE world_id=? AND source_ref=? AND chunk_index=?",
                       (shard_id, item.version_token, _json(item.source_metadata), now, world, item.source_ref, item.chunk_index))
            db.execute("DELETE FROM shards WHERE id=? AND NOT EXISTS(SELECT 1 FROM provenance WHERE shard_id=?)", (old_id, old_id))
            action = "updated"
        return shard_id, action, dedup

    def _get_shard(self, shard_id: str, *, action: str = "stored", deduplicated: bool = False,
                   score: int = 0) -> MemoryShard:
        with self._connection() as db:
            row = db.execute("SELECT * FROM shards WHERE id=?", (shard_id,)).fetchone()
            prov = db.execute("SELECT * FROM provenance WHERE shard_id=? ORDER BY source_ref,chunk_index", (shard_id,)).fetchall()
        if row is None:
            raise KeyError(f"unknown Memory Shard: {shard_id}")
        sources = tuple(SourceProvenance(p["source_ref"], p["chunk_index"], p["version_token"], json.loads(p["metadata"]), p["first_seen_at"], p["last_seen_at"]) for p in prov)
        return MemoryShard(row["id"], row["world_id"], row["sector_id"], row["content"], row["content_hash"],
                           row["route_hint"], tuple(json.loads(row["transformations"])), json.loads(row["privacy"]),
                           json.loads(row["metadata"]), sources, row["created_at"], row["updated_at"], score, action, deduplicated)

    def search(self, query: str, *, world: str, sector: str | None = None, limit: int = 10) -> list[MemoryShard]:
        self.get_world(world)
        if sector is not None:
            self.get_sector(world, sector)
        terms = [term.casefold() for term in re.findall(r"\w+", _required(query, "query"), re.UNICODE)]
        if limit < 1:
            raise ValueError("limit must be positive")
        sql, params = "SELECT id,content FROM shards WHERE world_id=?", [world]
        if sector is not None:
            sql, params = sql + " AND sector_id=?", params + [sector]
        with self._connection() as db:
            rows = db.execute(sql, params).fetchall()
        ranked = []
        phrase = query.casefold()
        for row in rows:
            text = row["content"].casefold()
            score = sum(text.count(term) for term in terms) + (5 if phrase in text else 0)
            if score:
                ranked.append((score, row["id"]))
        ranked.sort(key=lambda value: (-value[0], value[1]))
        return [self._get_shard(shard_id, action="matched", score=score) for score, shard_id in ranked[:limit]]

    def shards(self, *, world: str, sector: str | None = None) -> list[MemoryShard]:
        """Return all shards in one explicit scope for provider retrieval."""
        self.get_world(world)
        with self._connection() as db:
            if sector is None:
                rows = db.execute("SELECT id FROM shards WHERE world_id=? ORDER BY id", (world,)).fetchall()
            else:
                rows = db.execute("SELECT id FROM shards WHERE world_id=? AND sector_id=? ORDER BY id", (world, sector)).fetchall()
        return [self._get_shard(row["id"]) for row in rows]

    def browse_memories(
        self,
        *,
        world: str,
        query: str = "",
        sector: str | None = None,
        source: str | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> dict[str, Any]:
        """Browse one explicit memory scope with stable filters and pagination."""
        self.get_world(world)
        if sector:
            self.get_sector(world, sector)
        offset = max(0, int(offset))
        limit = max(1, min(int(limit), 100))
        where, params = ["s.world_id=?"], [world]
        if sector:
            where.append("s.sector_id=?")
            params.append(sector)
        if query.strip():
            where.append("LOWER(s.content) LIKE ?")
            params.append(f"%{query.strip().casefold()}%")
        if source:
            where.append("EXISTS(SELECT 1 FROM provenance pf WHERE pf.shard_id=s.id AND LOWER(pf.source_ref) LIKE ?)")
            params.append(f"%{source.casefold()}%")
        predicate = " AND ".join(where)
        with self._connection() as db:
            total = int(db.execute(f"SELECT COUNT(*) FROM shards s WHERE {predicate}", params).fetchone()[0])
            rows = db.execute(
                f"SELECT s.id FROM shards s WHERE {predicate} ORDER BY s.updated_at DESC,s.id LIMIT ? OFFSET ?",
                [*params, limit, offset],
            ).fetchall()
            sector_rows = db.execute(
                "SELECT id,display_name FROM sectors WHERE world_id=? ORDER BY display_name,id", (world,)
            ).fetchall()
            source_rows = db.execute(
                "SELECT p.source_ref,MAX(p.last_seen_at) AS latest FROM provenance p JOIN shards s ON s.id=p.shard_id "
                "WHERE s.world_id=? GROUP BY p.source_ref ORDER BY latest DESC,p.source_ref LIMIT 200", (world,),
            ).fetchall()
        return {
            "world": world,
            "query": query,
            "sector": sector,
            "source": source,
            "offset": offset,
            "limit": limit,
            "total": total,
            "has_more": offset + len(rows) < total,
            "items": [self._get_shard(row["id"]) for row in rows],
            "sectors": [{"id": row["id"], "name": row["display_name"]} for row in sector_rows],
            "sources": [str(row["source_ref"]) for row in source_rows],
        }

    def forget(self, shard_id: str, *, world: str) -> dict[str, Any]:
        """Delete one memory and its provenance inside the explicit World scope."""
        self.get_world(world)
        with self._connection() as db:
            row = db.execute("SELECT id FROM shards WHERE id=? AND world_id=?", (shard_id, world)).fetchone()
            if row is None:
                raise KeyError(f"unknown Memory Shard in {world}: {shard_id}")
            sources = int(db.execute("SELECT COUNT(*) FROM provenance WHERE shard_id=?", (shard_id,)).fetchone()[0])
            db.execute("DELETE FROM provenance WHERE shard_id=?", (shard_id,))
            db.execute("DELETE FROM shards WHERE id=? AND world_id=?", (shard_id, world))
        return {"forgotten": True, "id": shard_id, "world": world, "sources_removed": sources}

    def set_checkpoint(self, *, world: str, mode: str, source_ref: str, cursor: str,
                       sector: str | None = None) -> Checkpoint:
        self.get_world(world)
        if sector is not None: self.get_sector(world, sector)
        mode, source_ref, cursor = (_required(mode, "mode"), _required(source_ref, "source_ref"), str(cursor))
        now = _now()
        with self._connection() as db:
            db.execute("INSERT INTO checkpoints VALUES(?,?,?,?,?,?) ON CONFLICT(world_id, sector_id, mode, source_ref) DO UPDATE SET cursor=excluded.cursor,updated_at=excluded.updated_at",
                       (world, sector or "", mode, source_ref, cursor, now))
        return Checkpoint(world, sector, mode, source_ref, cursor, now)

    def get_checkpoint(self, *, world: str, mode: str, source_ref: str, sector: str | None = None) -> Checkpoint | None:
        with self._connection() as db:
            row = db.execute("SELECT * FROM checkpoints WHERE world_id=? AND sector_id=? AND mode=? AND source_ref=?", (world, sector or "", mode, source_ref)).fetchone()
        return Checkpoint(row["world_id"], row["sector_id"] or None, row["mode"], row["source_ref"], row["cursor"], row["updated_at"]) if row else None

    checkpoint = set_checkpoint

    def record_diary(self, content: str, *, world: str, title: str = "", sector: str | None = None,
                     occurred_at: str | None = None, kind: str = "episode", metadata: Mapping[str, Any] | None = None,
                     source_ref: str = "diary", version_token: str | None = None) -> DiaryRecord:
        self.get_world(world)
        if sector is not None: self.get_sector(world, sector)
        content, source_ref = _required(content, "content"), _required(source_ref, "source_ref")
        occurred_at, now = occurred_at or _now(), _now()
        diary_id = hashlib.sha256(f"{world}\0{sector or ''}\0{content}".encode()).hexdigest()[:32]
        with self._connection() as db:
            db.execute("INSERT INTO diary VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(world_id, sector_id, content) DO UPDATE SET title=excluded.title,metadata=excluded.metadata,occurred_at=excluded.occurred_at",
                       (diary_id, world, sector or "", occurred_at, title or content[:80], content, kind, _json(metadata), now))
            db.execute("INSERT INTO diary_provenance(diary_id,source_ref,chunk_index,version_token,metadata,first_seen_at,last_seen_at) VALUES(?,?,?,?,?,?,?) ON CONFLICT(diary_id,source_ref,chunk_index) DO UPDATE SET version_token=excluded.version_token,last_seen_at=excluded.last_seen_at",
                       (diary_id, source_ref, 0, version_token, "{}", now, now))
        return self._get_diary(diary_id)

    diary = record_diary

    def _get_diary(self, diary_id: str) -> DiaryRecord:
        with self._connection() as db:
            row = db.execute("SELECT * FROM diary WHERE id=?", (diary_id,)).fetchone()
            prov = db.execute("SELECT * FROM diary_provenance WHERE diary_id=?", (diary_id,)).fetchall()
        return DiaryRecord(row["id"], row["world_id"], row["sector_id"] or None, row["occurred_at"], row["title"], row["content"], row["kind"], json.loads(row["metadata"]), tuple(SourceProvenance(p["source_ref"], p["chunk_index"], p["version_token"], json.loads(p["metadata"]), p["first_seen_at"], p["last_seen_at"]) for p in prov))

    def timeline(self, *, world: str, sector: str | None = None, limit: int = 100) -> list[DiaryRecord]:
        self.get_world(world)
        with self._connection() as db:
            if sector is None:
                rows = db.execute("SELECT id FROM diary WHERE world_id=? ORDER BY occurred_at DESC LIMIT ?", (world, limit)).fetchall()
            else:
                rows = db.execute("SELECT id FROM diary WHERE world_id=? AND sector_id=? ORDER BY occurred_at DESC LIMIT ?", (world, sector, limit)).fetchall()
        return [self._get_diary(row["id"]) for row in rows]

    def consolidate(self, *, world: str, sector: str | None = None) -> dict[str, int]:
        records = self.timeline(world=world, sector=sector, limit=100000)
        seen: dict[str, DiaryRecord] = {}; removed = 0
        with self._connection() as db:
            for record in records:
                key = re.sub(r"\W+", " ", record.content.casefold()).strip()
                if key in seen and seen[key].id != record.id:
                    db.execute("DELETE FROM diary WHERE id=?", (record.id,)); removed += 1
                else: seen[key] = record
        return {"world": world, "sector": sector, "examined": len(records), "merged": removed, "remaining": len(records) - removed}

    def sync(self, world: str, records: Iterable[ShardInput | Mapping[str, Any]], *,
             sector: str | None = None, prune: bool = True) -> SyncReport:
        self.get_world(world)
        if sector is not None:
            self.get_sector(world, sector)
        items = [record if isinstance(record, ShardInput) else ShardInput(**record) for record in records]
        counts = {"inserted": 0, "updated": 0, "unchanged": 0, "deduplicated": 0}
        seen: set[tuple[str, int]] = set()
        with self._connection() as db:
            for item in items:
                if item.sector is None and sector is not None:
                    item = ShardInput(**{**item.__dict__, "sector": sector})
                if sector is not None and item.sector != sector:
                    raise ValueError("sync record Sector is outside the requested scope")
                shard_id, action, _ = self._ingest(db, item, world)
                del shard_id
                counts[action] += 1
                seen.add((item.source_ref, item.chunk_index))
            removed_sources = removed_shards = 0
            if prune:
                rows = db.execute("SELECT p.id,p.shard_id,p.source_ref,p.chunk_index FROM provenance p JOIN shards s ON s.id=p.shard_id WHERE p.world_id=? AND s.sector_id IS ?", (world, sector)).fetchall()
                doomed = [row for row in rows if (row["source_ref"], row["chunk_index"]) not in seen]
                for row in doomed:
                    db.execute("DELETE FROM provenance WHERE id=?", (row["id"],))
                    removed_sources += 1
                    before = db.total_changes
                    db.execute("DELETE FROM shards WHERE id=? AND NOT EXISTS(SELECT 1 FROM provenance WHERE shard_id=?)", (row["shard_id"], row["shard_id"]))
                    removed_shards += db.total_changes - before
        return SyncReport(world, sector, removed_sources=removed_sources, removed_shards=removed_shards, **counts)

    def status(self, world: str | None = None) -> dict[str, Any]:
        with self._connection() as db:
            where, params = (" WHERE world_id=?", (world,)) if world else ("", ())
            if world and db.execute("SELECT 1 FROM worlds WHERE id=?", (world,)).fetchone() is None:
                raise KeyError(f"unknown World: {world}")
            worlds = db.execute("SELECT COUNT(*) FROM worlds" + (" WHERE id=?" if world else ""), params).fetchone()[0]
            sectors = db.execute("SELECT COUNT(*) FROM sectors" + where, params).fetchone()[0]
            shards = db.execute("SELECT COUNT(*) FROM shards" + where, params).fetchone()[0]
            sources = db.execute("SELECT COUNT(*) FROM provenance" + where, params).fetchone()[0]
        return {"ready": True, "backend": "sqlite", "schema_version": SCHEMA_VERSION,
                "database": str(self.path), "world": world, "worlds": worlds,
                "sectors": sectors, "memory_shards": shards, "source_references": sources}

    check = status


Animus = MemoryStore
AnimusStore = MemoryStore
NativeAnimus = MemoryStore


__all__ = ["MemoryStore", "Animus", "AnimusStore", "NativeAnimus", "MemoryShard", "Sector", "DiaryRecord", "Checkpoint",
           "ShardInput", "SourceProvenance", "SyncReport", "World"]
