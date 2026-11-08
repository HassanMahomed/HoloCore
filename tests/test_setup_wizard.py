from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from holocore.config import Config
from holocore.local_models import LocalModelManager
from holocore.setup_app import (
    UNINSTALL_REGISTRY_KEY,
    _configure_windows_shell,
    initial_native_state,
    install_embedded_release,
    run_native_ui_smoke_test,
    stage_native_uninstaller,
    uninstall_native_installation,
)
from holocore.setup_wizard import (
    apply_setup,
    build_setup_plan,
    build_setup_state,
    render_setup_html,
)


def test_setup_state_detects_clients_and_models_without_writing(tmp_path: Path) -> None:
    home = tmp_path / "Second Brain"
    project = tmp_path / "My Project"
    discovery = {
        "candidates": [{"provider": "ollama", "reachable": True, "models": [{"name": "qwen3:8b"}]}],
        "fallback": "deterministic",
    }
    clients = {
        "claude": {"installed": True, "evidence": ["claude.exe"]},
        "codex": {"installed": False, "evidence": []},
        "cursor": {"installed": False, "evidence": []},
        "gemini": {"installed": False, "evidence": []},
        "opencode": {"installed": False, "evidence": []},
    }
    with patch("holocore.setup_wizard.detect_installed_clients", return_value=clients), patch.object(
        LocalModelManager, "discover", return_value=discovery
    ):
        state = build_setup_state(home=home, project=project)

    assert state["defaults"]["clients"] == ["claude"]
    assert state["models"] == discovery
    assert not home.exists()
    assert not project.exists()


def test_setup_plan_resolves_paths_and_requires_project_for_clients(tmp_path: Path) -> None:
    home = tmp_path / "brain"
    project = tmp_path / "project"
    plan = build_setup_plan(
        {"home": str(home), "project": str(project), "clients": ["codex"], "build_code_map": True}
    )

    assert plan["paths"]["Knowledge vault"] == str(home / "Knowledge")
    assert plan["paths"]["Code Map"] == str(home / "Projects" / "project" / "CodeMap" / "graph.json")
    assert any("Codex" in action for action in plan["actions"])
    assert not home.exists()

    with pytest.raises(ValueError, match="project folder"):
        build_setup_plan({"home": str(home), "clients": ["codex"]})


def test_apply_setup_uses_normal_home_and_project_initialization(
    tmp_path: Path, isolated_holocore_home: Path
) -> None:
    project = tmp_path / "project"
    result = apply_setup(
        {
            "home": str(isolated_holocore_home),
            "project": str(project),
            "clients": [],
            "build_code_map": False,
            "model": "",
        }
    )

    assert result["applied"] is True
    assert result["project"]["world_id"] == "project"
    assert Config.load(root=project).home == isolated_holocore_home
    assert not (isolated_holocore_home / "Projects" / "project" / "CodeMap" / "graph.json").exists()


def test_apply_setup_journals_failure_and_resumes_completed_steps(tmp_path: Path) -> None:
    home = tmp_path / "Second Brain"
    payload = {"home": str(home), "project": "", "clients": [], "model": "", "auto_update": True}
    with patch("holocore.setup_wizard.configure_auto_update", side_effect=OSError("simulated interruption")):
        with pytest.raises(RuntimeError, match="can be resumed"):
            apply_setup(payload)

    journal_path = home / "Runtime" / "setup-operation.json"
    failed = json.loads(journal_path.read_text(encoding="utf-8"))
    assert failed["state"] == "failed"
    assert failed["current_step"] == "auto_update"
    assert failed["completed_steps"] == ["home", "project", "local_model"]

    with patch("holocore.setup_wizard.configure_auto_update", return_value={"enabled": True}) as update:
        result = apply_setup(payload)

    assert result["resumed"] is True
    assert update.call_count == 1
    completed = json.loads(journal_path.read_text(encoding="utf-8"))
    assert completed["state"] == "complete"
    assert completed["completed_steps"] == ["home", "project", "local_model", "auto_update"]


def test_setup_html_is_local_accessible_and_review_first(tmp_path: Path) -> None:
    state = {
        "home": str(tmp_path / "HoloCore"), "project": "",
        "clients": {name: {"installed": False, "evidence": []} for name in ("claude", "codex", "cursor", "gemini", "opencode")},
        "models": {"candidates": [{"reachable": False, "models": []}]},
        "defaults": {"clients": [], "build_code_map": True},
    }
    html = render_setup_html(state, "local-token")

    assert "Nothing changes until you review and apply" in html
    assert 'label for="home"' in html
    assert "prefers-reduced-motion" in html
    assert "https://" not in html
    assert "X-HoloCore-Token" in html


def test_native_setup_starts_without_detection_or_writes(tmp_path: Path) -> None:
    home = tmp_path / "Second Brain"
    project = tmp_path / "Project"

    state = initial_native_state(home, project)

    assert state["detecting"] is True
    assert state["home"] == str(home)
    assert state["project"] == str(project)
    assert not home.exists()
    assert not project.exists()


def test_native_setup_executable_installs_its_embedded_engine(tmp_path: Path) -> None:
    payload = tmp_path / "payload.exe"
    setup = tmp_path / "downloaded-setup.exe"
    installer = tmp_path / "install.ps1"
    install_dir = tmp_path / "Programs" / "HoloCore"
    payload.write_bytes(b"engine")
    setup.write_bytes(b"setup")
    installer.write_text("param([switch]$Uninstall)\n", encoding="utf-8")

    with patch("holocore.setup_app._configure_windows_shell", return_value={"path_changed": True, "shortcut": "shortcut"}):
        first = install_embedded_release(
            payload_path=payload, setup_executable=setup, install_dir=install_dir,
            installer_script_path=installer,
        )
        second = install_embedded_release(
            payload_path=payload, setup_executable=setup, install_dir=install_dir,
            installer_script_path=installer,
        )

    assert first["installed"] is True
    assert first["engine_changed"] is True
    assert first["setup_changed"] is True
    assert second["engine_changed"] is False
    assert second["setup_changed"] is False
    assert first["installer_changed"] is True
    assert second["installer_changed"] is False
    assert (install_dir / "holocore.exe").read_bytes() == b"engine"
    assert (install_dir / "HoloCore Setup.exe").read_bytes() == b"setup"
    assert (install_dir / "install.ps1").read_text(encoding="utf-8").startswith("param")


def test_native_uninstaller_removes_only_application_files(tmp_path: Path) -> None:
    install_dir = tmp_path / "Programs" / "HoloCore"
    home = tmp_path / "HoloCore Home"
    project = tmp_path / "Project"
    install_dir.mkdir(parents=True)
    home.mkdir()
    project.mkdir()
    (install_dir / "holocore.exe").write_bytes(b"engine")
    (install_dir / "install.ps1").write_text("param([switch]$Uninstall)\n", encoding="utf-8")
    (home / "memories.db").write_bytes(b"memory")
    (project / "source.py").write_text("print('kept')\n", encoding="utf-8")

    with patch(
        "holocore.setup_app._remove_windows_shell_integration",
        return_value={"path_changed": True, "shortcuts_removed": [], "registry_removed": True},
    ):
        result = uninstall_native_installation(install_dir)

    assert result["uninstalled"] is True
    assert result["data_preserved"] is True
    assert not install_dir.exists()
    assert (home / "memories.db").read_bytes() == b"memory"
    assert (project / "source.py").is_file()


def test_native_uninstaller_rejects_unrelated_directory(tmp_path: Path) -> None:
    unrelated = tmp_path / "Not HoloCore"
    unrelated.mkdir()
    (unrelated / "important.txt").write_text("keep", encoding="utf-8")

    with pytest.raises(RuntimeError, match="not a verified HoloCore installation"):
        uninstall_native_installation(unrelated)

    assert (unrelated / "important.txt").read_text(encoding="utf-8") == "keep"


def test_native_uninstaller_is_staged_outside_install_directory(tmp_path: Path) -> None:
    install_dir = tmp_path / "Programs" / "HoloCore"
    install_dir.mkdir(parents=True)
    setup = install_dir / "HoloCore Setup.exe"
    setup.write_bytes(b"setup")
    calls: list[tuple[list[str], dict]] = []

    with patch(
        "holocore.setup_app.subprocess.Popen",
        lambda command, **kwargs: calls.append((command, kwargs)) or SimpleNamespace(),
    ):
        result = stage_native_uninstaller(
            install_dir=install_dir, source_setup=setup, stage_dir=tmp_path / "staging",
        )

    worker = Path(result["worker"])
    assert worker.is_file()
    assert install_dir not in worker.parents
    assert calls[0][0][0] == str(worker)
    assert calls[0][0][1:3] == ["--uninstall-worker", "--install-dir"]
    assert calls[0][0][3] == str(install_dir.resolve())


@pytest.mark.skipif(os.name != "nt", reason="Windows registry integration")
def test_native_setup_registers_windows_installed_apps_entry(tmp_path: Path, monkeypatch) -> None:
    class Key:
        def __init__(self, path: str):
            self.path = path

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    class FakeWinreg:
        HKEY_CURRENT_USER = object()
        REG_SZ = 1
        REG_EXPAND_SZ = 2
        REG_DWORD = 4

        def __init__(self):
            self.values: dict[tuple[str, str], object] = {}

        def CreateKey(self, _root, path: str):
            return Key(path)

        def QueryValueEx(self, key: Key, name: str):
            if key.path == "Environment" and name == "Path":
                return "C:\\Existing", self.REG_EXPAND_SZ
            raise FileNotFoundError

        def SetValueEx(self, key: Key, name: str, _reserved, _value_type, value):
            self.values[(key.path, name)] = value

    fake = FakeWinreg()
    monkeypatch.setitem(sys.modules, "winreg", fake)
    monkeypatch.setenv("APPDATA", str(tmp_path / "AppData"))
    monkeypatch.setenv("PATH", "C:\\Existing")
    monkeypatch.setattr(
        "holocore.setup_app.subprocess.run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stderr=""),
    )
    install_dir = tmp_path / "Programs" / "HoloCore"
    install_dir.mkdir(parents=True)
    setup = install_dir / "HoloCore Setup.exe"
    setup.write_bytes(b"setup")

    result = _configure_windows_shell(install_dir, setup, version="1.0.1")

    assert result["registry_key"].endswith(UNINSTALL_REGISTRY_KEY)
    assert fake.values[(UNINSTALL_REGISTRY_KEY, "DisplayVersion")] == "1.0.1"
    assert fake.values[(UNINSTALL_REGISTRY_KEY, "UninstallString")] == f'"{setup}" --uninstall'
    assert fake.values[(UNINSTALL_REGISTRY_KEY, "QuietUninstallString")].endswith("--uninstall --quiet")


@pytest.mark.skipif(os.name != "nt", reason="native Tk smoke test is Windows release coverage")
def test_native_setup_walks_every_page_at_scaled_dpi() -> None:
    assert run_native_ui_smoke_test() == 0


@pytest.mark.skipif(os.name != "nt", reason="native Tk smoke test is Windows release coverage")
def test_native_uninstaller_renders_confirmation_and_completion() -> None:
    environment = os.environ.copy()
    environment["PYTHONPATH"] = os.pathsep.join(
        [str(Path(__file__).resolve().parents[1] / "src"), environment.get("PYTHONPATH", "")]
    ).rstrip(os.pathsep)
    completed = subprocess.run(
        [sys.executable, "-m", "holocore.setup_app", "--uninstall-ui-smoke-test"],
        env=environment,
        check=False,
    )
    assert completed.returncode == 0
