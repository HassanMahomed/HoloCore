from pathlib import Path

import pytest

from holocore.engine import HoloCoreEngine
from holocore.guided_workflows import run_guided_workflow


def test_research_and_architecture_workflows_join_scoped_read_only_evidence(
    tmp_path: Path, isolated_holocore_home: Path
) -> None:
    root = tmp_path / "workflow-world"
    engine = HoloCoreEngine(root)
    engine.initialize(git=False, home=isolated_holocore_home, platforms=[])
    (root / "billing.py").write_text(
        "class BillingLedger:\n    def reconcile_billing(self):\n        return True\n", encoding="utf-8"
    )
    engine.refresh()
    engine.router.archive.world.create(
        "wiki/billing-decision.md",
        "# Billing decision\n\nThe verified billing architecture uses an immutable ledger.",
    )
    engine.remember("Earlier billing research rejected mutable balances.", sector="project", source="test")
    knowledge_before = sorted(path.relative_to(engine.router.config.vault) for path in engine.router.config.vault.rglob("*.md"))

    research = run_guided_workflow(root, kind="research", question="billing architecture", sector="project")
    architecture = run_guided_workflow(root, kind="architecture", question="billing ledger", sector="project")

    assert research["read_only"] is True
    assert all(research["counts"][name] >= 1 for name in ("Code Map", "Knowledge", "Memories"))
    assert {item["source"] for item in research["evidence"]} == {"Code Map", "Knowledge", "Memories"}
    assert "Promote the reviewed conclusion" in research["markdown"]
    assert architecture["kind"] == "architecture"
    assert "constraints and trade-offs" in architecture["markdown"]
    knowledge_after = sorted(path.relative_to(engine.router.config.vault) for path in engine.router.config.vault.rglob("*.md"))
    assert knowledge_after == knowledge_before


def test_guided_workflow_rejects_empty_or_unknown_requests(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="workflow must be"):
        run_guided_workflow(tmp_path, kind="planning", question="topic")
    with pytest.raises(ValueError, match="Enter a research question"):
        run_guided_workflow(tmp_path, kind="research", question="")
