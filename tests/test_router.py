from pathlib import Path

import pytest

from holocore.router import RouteLoopError, Router


def _fresh(router: Router, monkeypatch) -> None:
    monkeypatch.setattr(router.atlas, "freshness", lambda: {"state": "fresh", "fresh": True})


def test_router_checks_then_runs_atlas_only_for_code_query(tmp_path: Path, monkeypatch) -> None:
    router = Router(tmp_path)
    calls: list[str] = []
    monkeypatch.setattr(router.atlas, "freshness", lambda: calls.append("check") or {"state": "fresh", "fresh": True})
    monkeypatch.setattr(router.atlas, "search", lambda q: calls.append("atlas") or [{"id": "symbol:router", "label": "Router", "kind": "class", "source_file": "src/router.py"}])
    monkeypatch.setattr(router.archive, "search", lambda q: calls.append("archive") or [])
    monkeypatch.setattr(router.animus, "search", lambda q, world=None, sector=None: calls.append("animus") or [])

    router.search("dependency path")

    assert calls == ["check", "atlas"]
    assert router.last_plan is not None
    assert router.last_plan.use_archive is False


def test_router_uses_graph_context_for_one_corresponding_archive_search(tmp_path: Path, monkeypatch) -> None:
    router = Router(tmp_path)
    _fresh(router, monkeypatch)
    archive_queries: list[str] = []
    monkeypatch.setattr(router.atlas, "search", lambda q: [{"id": "symbol:auth", "label": "AuthService", "qualified_name": "app.auth.AuthService", "source_file": "src/auth.py", "kind": "class"}])
    monkeypatch.setattr(router.archive, "search", lambda q: archive_queries.append(q) or [])

    router.search("architecture login dependency")

    assert len(archive_queries) == 1
    assert "AuthService" in archive_queries[0]
    assert "src/auth.py" in archive_queries[0]


def test_router_queries_animus_last_only_for_history(tmp_path: Path, monkeypatch) -> None:
    router = Router(tmp_path)
    calls: list[str] = []
    monkeypatch.setattr(router.atlas, "freshness", lambda: calls.append("check") or {"state": "fresh", "fresh": True})
    monkeypatch.setattr(router.atlas, "search", lambda q: calls.append("atlas") or [])
    monkeypatch.setattr(router.archive, "search", lambda q: calls.append("archive") or [])
    monkeypatch.setattr(router.animus, "search", lambda q, world=None, sector=None: calls.append("animus") or [])

    router.search("previous login error")

    assert calls == ["check", "atlas", "archive", "animus"]


def test_stale_atlas_is_reported_and_not_searched(tmp_path: Path, monkeypatch) -> None:
    router = Router(tmp_path)
    calls: list[str] = []
    monkeypatch.setattr(router.atlas, "freshness", lambda: calls.append("check") or {"state": "stale", "fresh": False})
    monkeypatch.setattr(router.atlas, "search", lambda q: calls.append("atlas") or [])
    monkeypatch.setattr(router.archive, "search", lambda q: calls.append("archive") or [])

    results = router.search("dependency path")

    assert calls == ["check", "archive"]
    assert any(result.source == "CHECK" and result.title == "Atlas stale" for result in results)


def test_recursive_route_is_rejected_instead_of_looping(tmp_path: Path, monkeypatch) -> None:
    router = Router(tmp_path)
    _fresh(router, monkeypatch)
    monkeypatch.setattr(router.atlas, "search", lambda q: router.search(q))

    with pytest.raises(RouteLoopError, match="recursive HoloCore route rejected"):
        router.search("dependency path")
