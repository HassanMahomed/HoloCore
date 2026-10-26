from pathlib import Path

from holocore.engine import HoloCoreEngine


def test_runtime_is_native_and_has_no_embedded_engines(tmp_path):
    engine = HoloCoreEngine(tmp_path)
    assert not hasattr(engine.router.config, "embedded_graphify")
    assert not hasattr(engine.router.config, "embedded_mempalace")
    assert engine.router.atlas.output.parent.name == "CodeMap"
    assert engine.router.atlas.output.is_relative_to(engine.router.config.home / "Projects")
    assert not (tmp_path / ".holocore").exists()
