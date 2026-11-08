import ast
import sqlite3
from pathlib import Path

import pytest

from holocore.memories import MemoryStore, SCHEMA_VERSION, ShardInput


def store(tmp_path):
    animus = MemoryStore(tmp_path)
    animus.create_world("demo", "Demo World")
    animus.create_sector("demo", "debugging")
    animus.create_sector("demo", "decisions")
    return animus


def test_legacy_animus_module_is_a_compatibility_shim() -> None:
    from holocore.animus import Animus

    assert Animus is MemoryStore


def test_scoped_ingest_search_and_provenance(tmp_path):
    animus = store(tmp_path)
    shard = animus.ingest("The cache failure was fixed by invalidation.", world="demo",
                          sector="debugging", source_ref="session://one", version_token="v1",
                          route_hint="history", transformations=("none",),
                          privacy={"scope": "project"})
    animus.ingest("Cache is an architecture decision.", world="demo", sector="decisions",
                  source_ref="session://two")
    found = animus.search("cache failure", world="demo", sector="debugging")
    assert [item.id for item in found] == [shard.id]
    assert found[0].provenance[0].source_ref == "session://one"
    assert found[0].route_hint == "history"
    assert found[0].transformations == ("none",)


def test_content_hash_deduplicates_and_source_update_replaces_stale_shard(tmp_path):
    animus = store(tmp_path)
    first = animus.ingest("same verbatim memory", world="demo", sector="debugging", source_ref="a")
    duplicate = animus.ingest("same verbatim memory", world="demo", sector="debugging", source_ref="b")
    assert duplicate.id == first.id
    assert duplicate.action == "deduplicated"
    assert {p.source_ref for p in duplicate.provenance} == {"a", "b"}

    updated = animus.update("new memory text", world="demo", sector="debugging", source_ref="a", version_token="v2")
    assert updated.action == "updated"
    assert animus.status("demo")["memory_shards"] == 2  # old content remains for source b
    assert animus.search("new memory", world="demo", sector="debugging")[0].provenance[0].version_token == "v2"


def test_sync_is_idempotent_and_prunes_only_its_scope(tmp_path):
    animus = store(tmp_path)
    records = [ShardInput("current episode", "file://current", sector="debugging")]
    first = animus.sync("demo", records, sector="debugging")
    second = animus.sync("demo", records, sector="debugging")
    assert first.inserted == 1
    assert second.unchanged == 1

    animus.ingest("durable episode", world="demo", sector="decisions", source_ref="file://decision")
    report = animus.sync("demo", [], sector="debugging")
    assert report.removed_shards == 1
    assert animus.status("demo")["memory_shards"] == 1


def test_requires_declared_world_and_sector(tmp_path):
    animus = MemoryStore(tmp_path)
    with pytest.raises(KeyError):
        animus.ingest("memory", world="missing", source_ref="source")
    animus.create_world("demo")
    with pytest.raises(KeyError):
        animus.search("memory", world="demo", sector="missing")


def test_memory_browser_filters_paginates_and_forgets_with_provenance(tmp_path):
    animus = store(tmp_path)
    first = animus.ingest(
        "Billing architecture uses an immutable ledger.", world="demo", sector="decisions",
        source_ref="session://architecture", privacy={"scope": "project"},
    )
    animus.ingest(
        "A billing timeout was fixed.", world="demo", sector="debugging",
        source_ref="session://debugging",
    )

    page = animus.browse_memories(
        world="demo", query="billing", sector="decisions", source="session://architecture", limit=1,
    )

    assert page["total"] == 1
    assert page["items"][0].id == first.id
    assert page["items"][0].privacy == {"scope": "project"}
    assert {item["id"] for item in page["sectors"]} == {"debugging", "decisions"}
    assert set(page["sources"]) == {"session://architecture", "session://debugging"}

    forgotten = animus.forget(first.id, world="demo")
    assert forgotten == {"forgotten": True, "id": first.id, "world": "demo", "sources_removed": 1}
    assert animus.browse_memories(world="demo", query="immutable")["total"] == 0
    with pytest.raises(KeyError):
        animus.forget(first.id, world="demo")


def test_rename_world_preserves_scoped_memory_and_provenance(tmp_path):
    animus = store(tmp_path)
    shard = animus.ingest(
        "A retained memory shard.",
        world="demo",
        sector="debugging",
        source_ref="session://rename",
    )

    assert animus.rename_world("demo", "Demo Project", "Demo Project") is True

    assert animus.get_world("Demo Project").display_name == "Demo Project"
    with pytest.raises(KeyError):
        animus.get_world("demo")
    renamed = animus.search("retained", world="Demo Project", sector="debugging")
    assert [item.id for item in renamed] == [shard.id]
    assert renamed[0].provenance[0].source_ref == "session://rename"


def test_rename_world_merges_empty_legacy_scope_into_existing_world(tmp_path):
    animus = MemoryStore(tmp_path)
    animus.create_world("legacy-hash", "Demo Project")
    animus.create_sector("legacy-hash", "extra")
    animus.create_world("Demo Project", "Demo Project")

    assert animus.rename_world("legacy-hash", "Demo Project", "Demo Project") is True

    assert animus.get_sector("Demo Project", "extra").display_name == "extra"
    with pytest.raises(KeyError):
        animus.get_world("legacy-hash")


def test_native_module_has_no_external_engine_or_subprocess_imports():
    path = Path(__file__).parents[1] / "src" / "holocore" / "memories.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imports = {alias.name.split(".")[0] for node in ast.walk(tree)
               if isinstance(node, (ast.Import, ast.ImportFrom))
               for alias in (node.names if isinstance(node, ast.Import) else [ast.alias(node.module or "")])}
    assert "subprocess" not in imports
    assert "mempalace" not in imports


def test_schema_v1_migrates_with_backup_journal_and_preserved_data(tmp_path):
    database = tmp_path / "legacy.db"
    with sqlite3.connect(database) as db:
        db.executescript(
            """
            CREATE TABLE schema_info(version INTEGER NOT NULL);
            INSERT INTO schema_info VALUES(1);
            CREATE TABLE worlds(
                id TEXT PRIMARY KEY, display_name TEXT NOT NULL, metadata TEXT NOT NULL,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
            CREATE TABLE sectors(
                world_id TEXT NOT NULL REFERENCES worlds(id) ON DELETE CASCADE,
                id TEXT NOT NULL, display_name TEXT NOT NULL, metadata TEXT NOT NULL,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                PRIMARY KEY(world_id, id));
            CREATE TABLE shards(
                id TEXT PRIMARY KEY, world_id TEXT NOT NULL REFERENCES worlds(id) ON DELETE CASCADE,
                sector_id TEXT, content TEXT NOT NULL, content_hash TEXT NOT NULL,
                route_hint TEXT, transformations TEXT NOT NULL, privacy TEXT NOT NULL,
                metadata TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                FOREIGN KEY(world_id, sector_id) REFERENCES sectors(world_id, id));
            CREATE TABLE provenance(
                id INTEGER PRIMARY KEY, shard_id TEXT NOT NULL REFERENCES shards(id) ON DELETE CASCADE,
                world_id TEXT NOT NULL REFERENCES worlds(id) ON DELETE CASCADE,
                source_ref TEXT NOT NULL, chunk_index INTEGER NOT NULL, version_token TEXT,
                metadata TEXT NOT NULL, first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL,
                UNIQUE(world_id, source_ref, chunk_index));
            CREATE TABLE checkpoints(
                world_id TEXT NOT NULL REFERENCES worlds(id) ON DELETE CASCADE,
                sector_id TEXT NOT NULL DEFAULT '', mode TEXT NOT NULL, source_ref TEXT NOT NULL,
                cursor TEXT NOT NULL, updated_at TEXT NOT NULL,
                PRIMARY KEY(world_id, sector_id, mode, source_ref));
            INSERT INTO worlds VALUES('legacy','Legacy','{}','now','now');
            INSERT INTO sectors VALUES('legacy','project','Project','{}','now','now');
            INSERT INTO shards VALUES('s1','legacy','project','retained','hash',NULL,'[]','{}','{}','now','now');
            """
        )

    animus = MemoryStore(database)

    assert animus.migration_report["from_version"] == 1
    assert [item["version"] for item in animus.migration_report["applied"]] == [2, 3, 4]
    backup = Path(animus.migration_report["backup"])
    assert backup.is_file()
    with sqlite3.connect(backup) as db:
        assert db.execute("SELECT version FROM schema_info").fetchone()[0] == 1
        assert db.execute("SELECT content FROM shards").fetchone()[0] == "retained"
    with sqlite3.connect(database) as db:
        assert db.execute("SELECT version FROM schema_info").fetchone()[0] == SCHEMA_VERSION
        assert db.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        assert [row[0] for row in db.execute("SELECT version FROM schema_migrations ORDER BY version")] == [2, 3, 4]
        assert db.execute("SELECT content FROM shards").fetchone()[0] == "retained"


def test_future_schema_is_rejected_without_mutation(tmp_path):
    database = tmp_path / "future.db"
    with sqlite3.connect(database) as db:
        db.execute("CREATE TABLE schema_info(version INTEGER NOT NULL)")
        db.execute("INSERT INTO schema_info VALUES(?)", (SCHEMA_VERSION + 1,))

    with pytest.raises(RuntimeError, match="unsupported Memories schema version"):
        MemoryStore(database)

    with sqlite3.connect(database) as db:
        assert db.execute("SELECT version FROM schema_info").fetchone()[0] == SCHEMA_VERSION + 1
        assert db.execute("SELECT name FROM sqlite_master WHERE name='worlds'").fetchone() is None
