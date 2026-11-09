"""Native Windows first-run setup application for HoloCore."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import tkinter as tk
import uuid
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any

from holocore import __version__
from holocore.home import HomeManager
from holocore.obsidian_plugin import obsidian_vaults, remove_from_all_vaults
from holocore.setup_wizard import (
    CLIENT_NAMES,
    apply_setup,
    build_setup_plan,
    build_setup_state,
)

COLORS = {
    "navy": "#0F172A", "navy_2": "#1E293B", "paper": "#F8FAFC",
    "panel": "#FFFFFF", "ink": "#0F172A", "muted": "#475569",
    "line": "#CBD5E1", "soft": "#E2E8F0", "accent": "#16A34A",
    "accent_hover": "#15803D", "error": "#B91C1C", "blue": "#2563EB",
}

CLIENT_LABELS = {
    "claude": "Claude Code", "codex": "Codex", "cursor": "Cursor",
    "gemini": "Gemini CLI", "opencode": "OpenCode",
}

UNINSTALL_REGISTRY_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\HoloCore"
CONTROL_CENTRE_SHORTCUT = "HoloCore Control Centre.lnk"


def _copy_if_changed(source: Path, target: Path) -> bool:
    """Atomically copy one bundled executable without rewriting identical bytes."""
    if target.is_file() and source.stat().st_size == target.stat().st_size:
        if source.read_bytes() == target.read_bytes():
            return False
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".new")
    shutil.copy2(source, temporary)
    os.replace(temporary, target)
    return True


def _default_install_dir() -> Path:
    return (Path(os.environ.get("LOCALAPPDATA", Path.home())) / "Programs/HoloCore").resolve()


def _programs_dir() -> Path:
    return Path(os.environ.get("APPDATA", Path.home() / "AppData/Roaming")) / "Microsoft/Windows/Start Menu/Programs"


def _configure_windows_shell(
    install_dir: Path, setup_executable: Path, *, version: str = __version__
) -> dict[str, Any]:
    """Register PATH, Start menu actions, and the Windows Installed Apps entry."""
    if os.name != "nt":
        return {"path_changed": False, "shortcut": None, "uninstall_shortcut": None, "registry_key": None}
    import winreg

    path_changed = False
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
        try:
            current, value_type = winreg.QueryValueEx(key, "Path")
        except FileNotFoundError:
            current, value_type = "", winreg.REG_EXPAND_SZ
        entries = [item for item in str(current).split(";") if item]
        if str(install_dir).casefold() not in {item.casefold() for item in entries}:
            winreg.SetValueEx(key, "Path", 0, value_type, ";".join([*entries, str(install_dir)]))
            path_changed = True
    os.environ["PATH"] = str(install_dir) + os.pathsep + os.environ.get("PATH", "")
    programs = _programs_dir()
    shortcut = programs / "HoloCore Setup.lnk"
    uninstall_shortcut = programs / "Uninstall HoloCore.lnk"
    desktop_shortcut = programs / CONTROL_CENTRE_SHORTCUT
    quote = lambda value: "'" + str(value).replace("'", "''") + "'"
    script = (
        "$w=New-Object -ComObject WScript.Shell;"
        "$s=$w.CreateShortcut(" + quote(shortcut) + ");"
        "$s.TargetPath=" + quote(setup_executable) + ";"
        "$s.WorkingDirectory=" + quote(install_dir) + ";"
        "$s.Description='Configure HoloCore folders, AI tools, features, and local models';$s.Save();"
        "$c=$w.CreateShortcut(" + quote(desktop_shortcut) + ");"
        "$c.TargetPath=" + quote(setup_executable) + ";"
        "$c.Arguments='--desktop';"
        "$c.WorkingDirectory=" + quote(install_dir) + ";"
        "$c.Description='Browse HoloCore projects, knowledge, memories, and AI connections';$c.Save();"
        "$u=$w.CreateShortcut(" + quote(uninstall_shortcut) + ");"
        "$u.TargetPath=" + quote(setup_executable) + ";"
        "$u.Arguments='--uninstall';"
        "$u.WorkingDirectory=" + quote(install_dir) + ";"
        "$u.Description='Uninstall HoloCore while preserving Home, memories, Knowledge, and projects';$u.Save()"
    )
    completed = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True, text=True, check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if completed.returncode:
        raise OSError(completed.stderr.strip() or "could not create the HoloCore Start menu shortcuts")

    estimated_size = sum(path.stat().st_size for path in install_dir.iterdir() if path.is_file()) // 1024
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, UNINSTALL_REGISTRY_KEY) as key:
        values = {
            "DisplayName": ("HoloCore", winreg.REG_SZ),
            "DisplayVersion": (version, winreg.REG_SZ),
            "DisplayIcon": (str(setup_executable), winreg.REG_SZ),
            "Publisher": ("HoloCore contributors", winreg.REG_SZ),
            "InstallLocation": (str(install_dir), winreg.REG_SZ),
            "UninstallString": (f'"{setup_executable}" --uninstall', winreg.REG_SZ),
            "QuietUninstallString": (f'"{setup_executable}" --uninstall --quiet', winreg.REG_SZ),
            "URLInfoAbout": ("https://github.com/HassanMahomed/HoloCore", winreg.REG_SZ),
            "NoModify": (1, winreg.REG_DWORD),
            "NoRepair": (1, winreg.REG_DWORD),
            "EstimatedSize": (max(1, estimated_size), winreg.REG_DWORD),
        }
        for name, (value, value_type) in values.items():
            winreg.SetValueEx(key, name, 0, value_type, value)
    return {
        "path_changed": path_changed,
        "shortcut": str(shortcut),
        "uninstall_shortcut": str(uninstall_shortcut),
        "desktop_shortcut": str(desktop_shortcut),
        "registry_key": rf"HKCU\{UNINSTALL_REGISTRY_KEY}",
    }


def _remove_windows_shell_integration(install_dir: Path) -> dict[str, Any]:
    """Remove only HoloCore-owned per-user Windows integration."""
    if os.name != "nt":
        return {"path_changed": False, "shortcuts_removed": [], "registry_removed": False}
    import winreg

    path_changed = False
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
        try:
            current, value_type = winreg.QueryValueEx(key, "Path")
        except FileNotFoundError:
            current, value_type = "", winreg.REG_EXPAND_SZ
        entries = [item for item in str(current).split(";") if item]
        retained = [item for item in entries if item.rstrip("\\").casefold() != str(install_dir).rstrip("\\").casefold()]
        if retained != entries:
            winreg.SetValueEx(key, "Path", 0, value_type, ";".join(retained))
            path_changed = True

    removed: list[str] = []
    for shortcut in (_programs_dir() / "HoloCore Setup.lnk", _programs_dir() / "Uninstall HoloCore.lnk",
                     _programs_dir() / CONTROL_CENTRE_SHORTCUT):
        if shortcut.exists():
            shortcut.unlink()
            removed.append(str(shortcut))
    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, UNINSTALL_REGISTRY_KEY)
        registry_removed = True
    except FileNotFoundError:
        registry_removed = False
    return {"path_changed": path_changed, "shortcuts_removed": removed, "registry_removed": registry_removed}


def _validate_uninstall_directory(destination: Path) -> None:
    """Reject broad or unrelated deletion targets before recursive removal."""
    protected = {
        Path(destination.anchor).resolve(),
        Path.home().resolve(),
        Path(os.environ.get("LOCALAPPDATA", Path.home())).resolve(),
        Path(os.environ.get("APPDATA", Path.home())).resolve(),
    }
    if destination in protected or destination.parent == destination:
        raise RuntimeError(f"Refusing to uninstall from protected path: {destination}")
    installer = destination / "install.ps1"
    application_markers = (destination / "holocore.exe", destination / "HoloCore Setup.exe")
    if not installer.is_file() or not any(marker.is_file() for marker in application_markers):
        raise RuntimeError(
            f"Refusing to remove a folder that is not a verified HoloCore installation: {destination}"
        )


def uninstall_native_installation(install_dir: str | Path | None = None) -> dict[str, Any]:
    """Remove installed application files and shell integration, preserving all user data."""
    destination = Path(install_dir or _default_install_dir()).resolve()
    _validate_uninstall_directory(destination)
    shell = _remove_windows_shell_integration(destination)
    plugins = remove_from_all_vaults()  # The plugin cannot work without the engine; notes are untouched.
    removed = destination.is_dir()
    if removed:
        shutil.rmtree(destination)
    return {"uninstalled": removed, "install_dir": str(destination), "data_preserved": True, **shell, **plugins}


def _queue_worker_cleanup(worker_path: Path) -> None:
    if os.name != "nt" or not worker_path.is_file():
        return
    escaped = str(worker_path).replace("'", "''")
    command = (
        f"Wait-Process -Id {os.getpid()} -ErrorAction SilentlyContinue; "
        f"Remove-Item -LiteralPath '{escaped}' -Force -ErrorAction SilentlyContinue"
    )
    subprocess.Popen(
        ["powershell.exe", "-NoProfile", "-WindowStyle", "Hidden", "-Command", command],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def stage_native_uninstaller(
    *, install_dir: str | Path | None = None, source_setup: str | Path | None = None,
    stage_dir: str | Path | None = None, quiet: bool = False,
) -> dict[str, Any]:
    """Copy the windowed uninstaller outside its install folder and launch it."""
    destination = Path(install_dir or _default_install_dir()).resolve()
    source = Path(source_setup or sys.executable).resolve()
    if not source.is_file():
        raise FileNotFoundError(f"HoloCore Setup was not found: {source}")
    staging = Path(stage_dir or tempfile.gettempdir()).resolve()
    staging.mkdir(parents=True, exist_ok=True)
    worker = staging / f"HoloCore-Uninstall-{uuid.uuid4().hex}.exe"
    shutil.copy2(source, worker)
    command = [str(worker), "--uninstall-worker", "--install-dir", str(destination)]
    if quiet:
        command.append("--quiet")
    subprocess.Popen(command, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return {"staged": True, "worker": str(worker), "install_dir": str(destination), "quiet": quiet}


def install_embedded_release(
    *,
    payload_path: str | Path | None = None,
    setup_executable: str | Path | None = None,
    install_dir: str | Path | None = None,
    installer_script_path: str | Path | None = None,
    configure_shell: bool = True,
) -> dict[str, Any]:
    """Install the engine carried inside the windowed release executable."""
    payload = Path(payload_path) if payload_path else resource_path("holocore-payload/holocore-windows-x64.exe")
    if not payload.is_file():
        return {"embedded": False, "installed": False}
    source_setup = Path(setup_executable or sys.executable).resolve()
    destination = Path(install_dir or _default_install_dir()).resolve()
    engine_target = destination / "holocore.exe"
    setup_target = destination / "HoloCore Setup.exe"
    installer_source = Path(installer_script_path) if installer_script_path else resource_path("holocore-assets/install.ps1")
    if not installer_source.is_file():
        installer_source = resource_path("install.ps1")
    if not installer_source.is_file():
        raise OSError("The bundled HoloCore uninstaller is missing.")
    installer_target = destination / "install.ps1"
    engine_changed = _copy_if_changed(payload, engine_target)
    setup_changed = False
    if source_setup != setup_target:
        setup_changed = _copy_if_changed(source_setup, setup_target)
    installer_changed = _copy_if_changed(installer_source, installer_target)
    shell = (
        _configure_windows_shell(destination, setup_target, version=__version__)
        if configure_shell
        else {"path_changed": False, "shortcut": None, "uninstall_shortcut": None, "registry_key": None}
    )
    return {
        "embedded": True, "installed": True, "install_dir": str(destination),
        "engine": str(engine_target), "setup": str(setup_target),
        "installer": str(installer_target), "engine_changed": engine_changed,
        "setup_changed": setup_changed, "installer_changed": installer_changed, **shell,
    }


def resource_path(relative: str) -> Path:
    """Resolve development and PyInstaller bundled resources."""
    bundle = Path(getattr(sys, "_MEIPASS", Path(__file__).parents[2]))
    return bundle / relative


def initial_native_state(home: str | Path | None = None, project: str | Path | None = None) -> dict[str, Any]:
    """Create the immediate, no-probe state used while background detection runs."""
    selected_home = HomeManager(home).home
    return {
        "home": str(selected_home),
        "project": str(Path(project).expanduser().resolve()) if project else "",
        "clients": {name: {"installed": False, "evidence": []} for name in CLIENT_NAMES},
        "models": {"candidates": [{"provider": "ollama", "reachable": False, "models": []}]},
        "defaults": {"clients": [], "build_code_map": True, "auto_update": False},
        "detecting": True,
    }


class NativeUninstallApp:
    """Small, explicit Windows uninstaller that preserves every HoloCore data folder."""

    def __init__(self, root: tk.Tk, *, install_dir: str | Path, worker_path: str | Path | None = None):
        self.root = root
        self.install_dir = Path(install_dir).resolve()
        self.worker_path = Path(worker_path).resolve() if worker_path else None
        self.running = False
        self.status_var = tk.StringVar(value="Ready to uninstall")
        self._icon: tk.PhotoImage | None = None
        self._configure_window()
        self._build()

    def _configure_window(self) -> None:
        self.root.title("Uninstall HoloCore")
        self.root.geometry("640x460")
        self.root.resizable(False, False)
        self.root.configure(bg=COLORS["paper"])
        self.root.protocol("WM_DELETE_WINDOW", self._close)
        icon = resource_path("holocore-assets/holocore-icon.png")
        if not icon.is_file():
            icon = resource_path("docs/assets/holocore-icon.png")
        try:
            source = tk.PhotoImage(file=str(icon))
            factor = max(1, source.width() // 64)
            self._icon = source.subsample(factor, factor)
            self.root.iconphoto(True, self._icon)
        except tk.TclError:
            self._icon = None

    def _build(self) -> None:
        self.body = tk.Frame(self.root, bg=COLORS["panel"], padx=42, pady=34)
        self.body.pack(fill="both", expand=True, padx=18, pady=18)
        tk.Label(
            self.body, text="Uninstall HoloCore", bg=COLORS["panel"], fg=COLORS["ink"],
            font=("Segoe UI Semibold", 24),
        ).pack(anchor="w")
        tk.Label(
            self.body,
            text="This removes the application from Windows. Your HoloCore Home, Knowledge, memories, chat history, and connected projects will remain untouched.",
            bg=COLORS["panel"], fg=COLORS["muted"], font=("Segoe UI", 10),
            wraplength=520, justify="left",
        ).pack(anchor="w", pady=(10, 22))
        detail = tk.Frame(self.body, bg="#FFF7ED", highlightbackground="#FDBA74", highlightthickness=1, padx=18, pady=16)
        detail.pack(fill="x")
        tk.Label(detail, text="Will be removed", bg="#FFF7ED", fg=COLORS["ink"], font=("Segoe UI Semibold", 11)).pack(anchor="w")
        tk.Label(
            detail,
            text="Application files · PATH entry · Start menu shortcuts · Installed Apps entry",
            bg="#FFF7ED", fg=COLORS["muted"], font=("Segoe UI", 9), wraplength=480, justify="left",
        ).pack(anchor="w", pady=(4, 0))
        self.progress = ttk.Progressbar(self.body, mode="indeterminate")
        self.progress.pack(fill="x", pady=(24, 8))
        tk.Label(self.body, textvariable=self.status_var, bg=COLORS["panel"], fg=COLORS["muted"], font=("Segoe UI", 9)).pack(anchor="w")
        actions = tk.Frame(self.body, bg=COLORS["panel"])
        actions.pack(side="bottom", fill="x", pady=(24, 0))
        self.cancel_button = ttk.Button(actions, text="Cancel", command=self._close)
        self.cancel_button.pack(side="left")
        self.uninstall_button = ttk.Button(actions, text="Uninstall HoloCore", command=self._confirm)
        self.uninstall_button.pack(side="right")

    def _confirm(self) -> None:
        confirmed = messagebox.askyesno(
            "Uninstall HoloCore?",
            "Remove the HoloCore application from this computer?\n\nYour Home, Knowledge, memories, and projects will be preserved.",
            parent=self.root,
        )
        if not confirmed:
            return
        self.running = True
        self.status_var.set("Removing HoloCore application files…")
        self.cancel_button.configure(state="disabled")
        self.uninstall_button.configure(state="disabled")
        self.progress.start(10)
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self) -> None:
        try:
            result = uninstall_native_installation(self.install_dir)
        except Exception as exc:
            self.root.after(0, lambda detail=str(exc): self._failed(detail))
            return
        self.root.after(0, lambda: self._complete(result))

    def _complete(self, result: dict[str, Any]) -> None:
        self.running = False
        self.progress.stop()
        self.status_var.set("HoloCore was removed. Your data was preserved.")
        self.uninstall_button.configure(text="Close", state="normal", command=self._close)
        self.cancel_button.configure(state="disabled")

    def _failed(self, detail: str) -> None:
        self.running = False
        self.progress.stop()
        self.status_var.set("Uninstall needs attention")
        self.cancel_button.configure(state="normal")
        self.uninstall_button.configure(state="normal")
        messagebox.showerror("HoloCore uninstall", detail, parent=self.root)

    def _close(self) -> None:
        if self.running:
            return
        if self.worker_path:
            _queue_worker_cleanup(self.worker_path)
        self.root.destroy()


class NativeSetupApp:
    """A native, progressive setup wizard that delegates writes to shared APIs."""

    def __init__(
        self, root: tk.Tk, *, home: str | Path | None = None,
        project: str | Path | None = None, start_discovery: bool = True,
    ):
        self.root = root
        self.state = initial_native_state(home, project)
        self.step = 0
        self.applying = False
        self.switched_to_control_centre = False
        self.plan: dict[str, Any] | None = None
        self.client_details = self.state["clients"]
        self.client_vars = {name: tk.BooleanVar(value=False) for name in CLIENT_NAMES}
        self.home_var = tk.StringVar(value=self.state["home"])
        self.project_var = tk.StringVar(value=self.state["project"])
        self.map_var = tk.BooleanVar(value=True)
        self.update_var = tk.BooleanVar(value=False)
        self.model_var = tk.StringVar(value="")
        self.vaults = [row for row in obsidian_vaults() if row["available"]]
        self.vault_vars = {row["path"]: tk.BooleanVar(value=False) for row in self.vaults}  # Opt-in per vault.
        self.status_var = tk.StringVar(value="Detecting AI tools and local models…")
        self._icon: tk.PhotoImage | None = None
        self._configure_window()
        self._configure_styles()
        self._build_shell()
        self.show_step(0)
        if start_discovery:
            threading.Thread(
                target=self._discover,
                args=(self.state["home"], self.state["project"]),
                daemon=True,
            ).start()

    def _configure_window(self) -> None:
        self.root.title("HoloCore Setup")
        self.root.geometry("980x720")
        self.root.minsize(820, 620)
        self.root.configure(bg=COLORS["paper"])
        self.root.protocol("WM_DELETE_WINDOW", self._close)
        icon = resource_path("holocore-assets/holocore-icon.png")
        if not icon.is_file():
            icon = resource_path("docs/assets/holocore-icon.png")
        try:
            source = tk.PhotoImage(file=str(icon))
            factor = max(1, source.width() // 64)
            self._icon = source.subsample(factor, factor)
            self.root.iconphoto(True, self._icon)
        except tk.TclError:
            self._icon = None

    def _configure_styles(self) -> None:
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure("TFrame", background=COLORS["paper"])
        style.configure("Panel.TFrame", background=COLORS["panel"])
        style.configure("Nav.TFrame", background=COLORS["navy"])
        style.configure("TLabel", background=COLORS["paper"], foreground=COLORS["ink"], font=("Segoe UI", 10))
        style.configure("Panel.TLabel", background=COLORS["panel"], foreground=COLORS["ink"], font=("Segoe UI", 10))
        style.configure("Title.TLabel", background=COLORS["panel"], foreground=COLORS["ink"], font=("Segoe UI Semibold", 24))
        style.configure("Subtitle.TLabel", background=COLORS["panel"], foreground=COLORS["muted"], font=("Segoe UI", 10))
        style.configure("Section.TLabel", background=COLORS["panel"], foreground=COLORS["ink"], font=("Segoe UI Semibold", 12))
        style.configure("NavTitle.TLabel", background=COLORS["navy"], foreground="#FFFFFF", font=("Segoe UI Semibold", 18))
        style.configure("NavText.TLabel", background=COLORS["navy"], foreground="#94A3B8", font=("Segoe UI", 10))
        style.configure("NavActive.TLabel", background=COLORS["navy_2"], foreground="#FFFFFF", font=("Segoe UI Semibold", 10), padding=(12, 11))
        style.configure("NavIdle.TLabel", background=COLORS["navy"], foreground="#94A3B8", font=("Segoe UI", 10), padding=(12, 11))
        style.configure("TEntry", fieldbackground="#FFFFFF", foreground=COLORS["ink"], bordercolor=COLORS["line"], padding=8)
        style.configure("TCombobox", fieldbackground="#FFFFFF", foreground=COLORS["ink"], bordercolor=COLORS["line"], padding=7)
        style.configure("TCheckbutton", background=COLORS["panel"], foreground=COLORS["ink"], font=("Segoe UI", 10))
        style.map("TCheckbutton", background=[("active", COLORS["panel"])])
        style.configure("Secondary.TButton", background="#FFFFFF", foreground=COLORS["ink"], bordercolor=COLORS["line"], font=("Segoe UI Semibold", 10), padding=(16, 9))
        style.map("Secondary.TButton", background=[("active", COLORS["soft"])])
        style.configure("Primary.TButton", background=COLORS["accent"], foreground="#FFFFFF", bordercolor=COLORS["accent"], font=("Segoe UI Semibold", 10), padding=(18, 9))
        style.map("Primary.TButton", background=[("active", COLORS["accent_hover"]), ("disabled", "#94A3B8")])
        style.configure("NavAction.TButton", background=COLORS["navy_2"], foreground="#FFFFFF", bordercolor="#334155", font=("Segoe UI Semibold", 9), padding=(12, 8))
        style.map("NavAction.TButton", background=[("active", "#334155")])
        style.configure("Horizontal.TProgressbar", background=COLORS["accent"], troughcolor=COLORS["soft"], bordercolor=COLORS["soft"])

    def _build_shell(self) -> None:
        self.root.grid_rowconfigure(0, weight=1)
        self.root.grid_columnconfigure(1, weight=1)
        nav = ttk.Frame(self.root, style="Nav.TFrame", width=238)
        nav.grid(row=0, column=0, sticky="nsew")
        nav.grid_propagate(False)
        ttk.Label(nav, text="HoloCore", style="NavTitle.TLabel").pack(anchor="w", padx=24, pady=(30, 2))
        ttk.Label(nav, text="PRIVATE · LOCAL · YOURS", style="NavText.TLabel").pack(anchor="w", padx=24, pady=(0, 28))
        self.nav_labels: list[ttk.Label] = []
        for index, label in enumerate(("Choose folders", "Select features", "Local AI model", "Review and install"), start=1):
            item = ttk.Label(nav, text=f"{index:02d}   {label}", style="NavIdle.TLabel")
            item.pack(fill="x", padx=12, pady=2)
            self.nav_labels.append(item)
        ttk.Label(nav, text="No cloud account required.\nNo project content is uploaded.", style="NavText.TLabel", justify="left").pack(side="bottom", anchor="w", padx=24, pady=(12, 28))
        self.uninstall_button = ttk.Button(nav, text="Uninstall HoloCore", style="NavAction.TButton", command=self._request_uninstall)
        self.uninstall_button.pack(side="bottom", fill="x", padx=24)
        ttk.Button(nav, text="Open control centre", style="NavAction.TButton",
                   command=self._open_desktop).pack(side="bottom", fill="x", padx=24, pady=8)

        outer = ttk.Frame(self.root, style="Panel.TFrame")
        outer.grid(row=0, column=1, sticky="nsew")
        outer.grid_rowconfigure(1, weight=1)
        outer.grid_columnconfigure(0, weight=1)
        top = ttk.Frame(outer, style="Panel.TFrame", padding=(36, 24, 36, 12))
        top.grid(row=0, column=0, sticky="ew")
        self.step_hint = ttk.Label(top, text="STEP 1 OF 4", style="Subtitle.TLabel")
        self.step_hint.pack(anchor="w")
        self.content = ttk.Frame(outer, style="Panel.TFrame", padding=(36, 10, 36, 16))
        self.content.grid(row=1, column=0, sticky="nsew")
        self.content.grid_columnconfigure(0, weight=1)
        self.content.grid_rowconfigure(0, weight=1)
        footer = ttk.Frame(outer, style="Panel.TFrame", padding=(36, 14, 36, 24))
        footer.grid(row=2, column=0, sticky="ew")
        footer.grid_columnconfigure(1, weight=1)
        self.back_button = ttk.Button(footer, text="Back", style="Secondary.TButton", command=self.back)
        self.back_button.grid(row=0, column=0, sticky="w")
        self.footer_status = ttk.Label(footer, textvariable=self.status_var, style="Subtitle.TLabel")
        self.footer_status.grid(row=0, column=1, padx=16, sticky="w")
        self.next_button = ttk.Button(footer, text="Continue", style="Primary.TButton", command=self.next)
        self.next_button.grid(row=0, column=2, sticky="e")

    def _open_desktop(self) -> None:
        if self.applying:
            return
        self.switched_to_control_centre = True
        from holocore.desktop_app import DesktopControlCentre

        for child in self.root.winfo_children():
            child.destroy()
        DesktopControlCentre(
            self.root, self.project_var.get().strip() or Path.cwd(),
            home=self.home_var.get().strip() or None,
        )

    def _clear_content(self) -> ttk.Frame:
        for child in self.content.winfo_children():
            child.destroy()
        body = ttk.Frame(self.content, style="Panel.TFrame")
        body.grid(row=0, column=0, sticky="nsew")
        body.grid_columnconfigure(0, weight=1)
        return body

    def _heading(self, body: ttk.Frame, title: str, subtitle: str) -> None:
        ttk.Label(body, text=title, style="Title.TLabel").pack(anchor="w")
        ttk.Label(body, text=subtitle, style="Subtitle.TLabel", wraplength=620, justify="left").pack(anchor="w", pady=(6, 24))

    def _field(self, body: ttk.Frame, label: str, variable: tk.StringVar, browse_command) -> None:
        ttk.Label(body, text=label, style="Section.TLabel").pack(anchor="w", pady=(10, 6))
        row = ttk.Frame(body, style="Panel.TFrame")
        row.pack(fill="x")
        row.grid_columnconfigure(0, weight=1)
        entry = ttk.Entry(row, textvariable=variable)
        entry.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        ttk.Button(row, text="Browse…", style="Secondary.TButton", command=browse_command).grid(row=0, column=1)

    def show_step(self, step: int) -> None:
        self.step = max(0, min(3, step))
        self.step_hint.configure(text=f"STEP {self.step + 1} OF 4")
        for index, item in enumerate(self.nav_labels):
            item.configure(style="NavActive.TLabel" if index == self.step else "NavIdle.TLabel")
        self.back_button.configure(state="disabled" if self.step == 0 else "normal")
        self.next_button.configure(text="Install HoloCore" if self.step == 3 else "Continue", state="normal")
        if self.step == 0:
            self._show_folders()
        elif self.step == 1:
            self._show_features()
        elif self.step == 2:
            self._show_model()
        else:
            self._show_review()

    def _show_folders(self) -> None:
        body = self._clear_content()
        self._heading(body, "Choose where HoloCore lives", "HoloCore Home holds your Second Brain. The project folder is the first code or document project HoloCore will connect.")
        self._field(body, "HoloCore Home", self.home_var, lambda: self._browse(self.home_var, "Choose HoloCore Home"))
        ttk.Label(body, text="The Knowledge subfolder can be opened directly in Obsidian.", style="Subtitle.TLabel").pack(anchor="w", pady=(6, 12))
        self._field(body, "First project", self.project_var, lambda: self._browse(self.project_var, "Choose your first project"))
        ttk.Label(body, text="Optional if you only want to create the Second Brain now. A project is required to connect AI tools.", style="Subtitle.TLabel", wraplength=620).pack(anchor="w", pady=(6, 0))

    def _scrollable(self, body: ttk.Frame) -> ttk.Frame:
        """A vertically scrolling area for pages longer than the window."""
        canvas = tk.Canvas(body, bg=COLORS["panel"], highlightthickness=0)
        scroll = ttk.Scrollbar(body, orient="vertical", command=canvas.yview)
        inner = ttk.Frame(canvas, style="Panel.TFrame")
        inner.bind("<Configure>", lambda _event: canvas.configure(scrollregion=canvas.bbox("all")))
        window = canvas.create_window((0, 0), window=inner, anchor="nw")
        canvas.bind("<Configure>", lambda event: canvas.itemconfigure(window, width=event.width))
        canvas.configure(yscrollcommand=scroll.set)
        canvas.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        wheel = lambda event: canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")
        canvas.bind("<Enter>", lambda _event: canvas.bind_all("<MouseWheel>", wheel))
        canvas.bind("<Leave>", lambda _event: canvas.unbind_all("<MouseWheel>"))
        return inner

    def _show_features(self) -> None:
        body = self._scrollable(self._clear_content())
        self._heading(body, "Select AI tools and features", "Detected tools are marked below. HoloCore changes only the integrations you select and preserves unrelated settings.")
        tools = ttk.Frame(body, style="Panel.TFrame")
        tools.pack(fill="x")
        for row_index, name in enumerate(CLIENT_NAMES):
            detail = self.client_details.get(name, {})
            installed = bool(detail.get("installed"))
            card = tk.Frame(tools, bg="#F8FAFC", highlightbackground=COLORS["line"], highlightthickness=1, padx=12, pady=9)
            card.grid(row=row_index, column=0, sticky="ew", pady=3)
            tools.grid_columnconfigure(0, weight=1)
            check = tk.Checkbutton(card, text=CLIENT_LABELS[name], variable=self.client_vars[name], bg="#F8FAFC", activebackground="#F8FAFC", fg=COLORS["ink"], selectcolor="#FFFFFF", font=("Segoe UI Semibold", 10), anchor="w")
            check.pack(side="left")
            badge = "Detected" if installed else "Not detected"
            color = COLORS["accent"] if installed else COLORS["muted"]
            tk.Label(card, text=badge, bg="#F8FAFC", fg=color, font=("Segoe UI", 9)).pack(side="right")
        ttk.Separator(body).pack(fill="x", pady=18)
        ttk.Checkbutton(body, text="Build the first Code Map now", variable=self.map_var).pack(anchor="w", pady=4)
        ttk.Label(body, text="Indexes project structure locally. It can also be built later.", style="Subtitle.TLabel").pack(anchor="w", padx=24)
        ttk.Checkbutton(body, text="Enable lightweight daily update checks", variable=self.update_var).pack(anchor="w", pady=(14, 4))
        ttk.Label(body, text="Checks only during active sessions; no resident background service.", style="Subtitle.TLabel").pack(anchor="w", padx=24)
        if self.vaults:
            ttk.Separator(body).pack(fill="x", pady=14)
            ttk.Label(body, text="Add the HoloCore Board to Obsidian", style="Section.TLabel").pack(anchor="w")
            ttk.Label(body, text="A read-only view of your projects, AI sessions, memories, and changed files. Uninstalling HoloCore removes it again.",
                      style="Subtitle.TLabel", wraplength=620, justify="left").pack(anchor="w", pady=(2, 4))
            for row in self.vaults:
                ttk.Checkbutton(body, text=f"{row['name']}   ({row['path']})" + ("  · already added" if row["installed"] else ""),
                                variable=self.vault_vars[row["path"]]).pack(anchor="w", pady=1)

    def _show_model(self) -> None:
        body = self._clear_content()
        candidate = self.state["models"]["candidates"][0]
        reachable = bool(candidate.get("reachable"))
        models = [str(item["name"]) for item in candidate.get("models", [])]
        self._heading(body, "Choose a local AI model", "A model is optional. HoloCore remains fully usable with deterministic extraction and no account or API key.")
        status = "Ollama detected on this computer" if reachable else ("Detection is still running…" if self.state.get("detecting") else "Ollama was not detected")
        color = COLORS["accent"] if reachable else COLORS["muted"]
        banner = tk.Frame(body, bg="#F0FDF4" if reachable else "#F8FAFC", highlightbackground=COLORS["line"], highlightthickness=1, padx=16, pady=14)
        banner.pack(fill="x", pady=(0, 22))
        tk.Label(banner, text=status, bg=banner["bg"], fg=color, font=("Segoe UI Semibold", 11)).pack(anchor="w")
        tk.Label(banner, text=f"{len(models)} installed model{'s' if len(models) != 1 else ''}" if reachable else "HoloCore will use its local deterministic baseline.", bg=banner["bg"], fg=COLORS["muted"], font=("Segoe UI", 9)).pack(anchor="w", pady=(3, 0))
        ttk.Label(body, text="Model used for local extraction", style="Section.TLabel").pack(anchor="w", pady=(0, 6))
        choices = ["Deterministic mode (no model)", *models]
        selected = self.model_var.get()
        combo = ttk.Combobox(body, state="readonly", values=choices)
        combo.pack(fill="x")
        combo.set(selected if selected in models else choices[0])
        combo.bind("<<ComboboxSelected>>", lambda _event: self.model_var.set("" if combo.current() == 0 else combo.get()))
        ttk.Label(body, text="HoloCore does not install Ollama, download models, or send project content over the network.", style="Subtitle.TLabel", wraplength=620).pack(anchor="w", pady=(9, 0))

    def _payload(self) -> dict[str, Any]:
        return {
            "home": self.home_var.get(), "project": self.project_var.get(),
            "clients": [name for name in CLIENT_NAMES if self.client_vars[name].get()],
            "build_code_map": self.map_var.get(), "auto_update": self.update_var.get(),
            "model": self.model_var.get(),
            "obsidian_vaults": [path for path, variable in self.vault_vars.items() if variable.get()],
        }

    def _show_review(self) -> None:
        body = self._clear_content()
        self._heading(body, "Review before installing", "Nothing below is applied until you select Install HoloCore.")
        try:
            self.plan = build_setup_plan(self._payload())
        except (OSError, ValueError) as exc:
            self.plan = None
            tk.Label(body, text=str(exc), bg="#FEF2F2", fg=COLORS["error"], font=("Segoe UI Semibold", 10), padx=14, pady=12, anchor="w").pack(fill="x")
            self.next_button.configure(state="disabled")
            return
        canvas = tk.Canvas(body, bg=COLORS["panel"], highlightthickness=0)
        scroll = ttk.Scrollbar(body, orient="vertical", command=canvas.yview)
        inner = ttk.Frame(canvas, style="Panel.TFrame")
        inner.bind("<Configure>", lambda _event: canvas.configure(scrollregion=canvas.bbox("all")))
        window = canvas.create_window((0, 0), window=inner, anchor="nw")
        canvas.bind("<Configure>", lambda event: canvas.itemconfigure(window, width=event.width))
        canvas.configure(yscrollcommand=scroll.set)
        canvas.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        ttk.Label(inner, text="Planned actions", style="Section.TLabel").pack(anchor="w")
        for action in self.plan["actions"]:
            ttk.Label(inner, text=f"✓  {action}", style="Panel.TLabel", wraplength=600, justify="left").pack(anchor="w", pady=3)
        ttk.Separator(inner).pack(fill="x", pady=16)
        ttk.Label(inner, text="Resolved locations", style="Section.TLabel").pack(anchor="w", pady=(0, 5))
        for label, path in self.plan["paths"].items():
            row = ttk.Frame(inner, style="Panel.TFrame")
            row.pack(fill="x", pady=4)
            ttk.Label(row, text=label, style="Panel.TLabel", width=22).pack(side="left", anchor="nw")
            ttk.Label(row, text=path, style="Subtitle.TLabel", wraplength=430, justify="left").pack(side="left", fill="x", expand=True)

    def _show_complete(self, result: dict[str, Any]) -> None:
        self.applying = False
        self.status_var.set("Setup complete")
        for item in self.nav_labels:
            item.configure(style="NavIdle.TLabel")
        self.step_hint.configure(text="COMPLETE")
        body = self._clear_content()
        panel = tk.Frame(body, bg="#F0FDF4", highlightbackground="#86EFAC", highlightthickness=1, padx=26, pady=26)
        panel.pack(fill="x", pady=(30, 0))
        tk.Label(panel, text="HoloCore is ready", bg="#F0FDF4", fg=COLORS["ink"], font=("Segoe UI Semibold", 24)).pack(anchor="w")
        tk.Label(panel, text=result["next"], bg="#F0FDF4", fg=COLORS["muted"], font=("Segoe UI", 10), wraplength=560, justify="left").pack(anchor="w", pady=(8, 0))
        self.back_button.configure(state="disabled")
        self.next_button.configure(text="Open control centre", state="normal", command=self._open_desktop)

    def _show_applying(self) -> None:
        body = self._clear_content()
        self._heading(body, "Installing HoloCore", "Creating local folders and applying only the integrations you selected.")
        progress = ttk.Progressbar(body, mode="indeterminate")
        progress.pack(fill="x", pady=20)
        progress.start(10)
        ttk.Label(body, text="Large projects can take a little longer while the first Code Map is built.", style="Subtitle.TLabel").pack(anchor="w")
        self.back_button.configure(state="disabled")
        self.next_button.configure(state="disabled")

    def _browse(self, variable: tk.StringVar, title: str) -> None:
        initial = Path(variable.get()).expanduser()
        selected = filedialog.askdirectory(parent=self.root, title=title, initialdir=str(initial if initial.is_dir() else initial.parent))
        if selected:
            variable.set(str(Path(selected).resolve()))

    def _discover(self, home: str, project: str) -> None:
        try:
            state = build_setup_state(home=home, project=project)
        except Exception as exc:  # Discovery is advisory; setup remains usable.
            self.root.after(0, lambda detail=str(exc): self._discovery_failed(detail))
            return
        self.root.after(0, lambda: self._discovery_ready(state))

    def _discovery_ready(self, state: dict[str, Any]) -> None:
        if self.switched_to_control_centre:
            return
        self.state = {**state, "detecting": False}
        self.client_details = state["clients"]
        if self.project_var.get().strip():
            for name in state["defaults"]["clients"]:
                self.client_vars[name].set(True)
        detected = sum(1 for detail in self.client_details.values() if detail.get("installed"))
        models = self.state["models"]["candidates"][0].get("models", [])
        self.status_var.set(f"Detected {detected} AI tools and {len(models)} local models")
        if self.step in {1, 2}:
            self.show_step(self.step)

    def _discovery_failed(self, detail: str) -> None:
        if self.switched_to_control_centre:
            return
        self.state["detecting"] = False
        self.status_var.set("Detection unavailable; manual setup is still available")

    def next(self) -> None:
        if self.applying:
            return
        if self.step < 3:
            self.show_step(self.step + 1)
            return
        if not self.plan:
            return
        self.applying = True
        self.status_var.set("Installing…")
        self._show_applying()
        payload = self._payload()
        threading.Thread(target=self._apply, args=(payload,), daemon=True).start()

    def back(self) -> None:
        if not self.applying and self.step > 0:
            self.show_step(self.step - 1)

    def _apply(self, payload: dict[str, Any]) -> None:
        try:
            result = apply_setup(payload)
        except Exception as exc:
            self.root.after(0, lambda detail=str(exc): self._apply_failed(detail))
            return
        self.root.after(0, lambda: self._show_complete(result))

    def _apply_failed(self, detail: str) -> None:
        self.applying = False
        self.status_var.set("Setup needs attention")
        messagebox.showerror("HoloCore setup", detail, parent=self.root)
        self.show_step(3)

    def _request_uninstall(self) -> None:
        if self.applying:
            return
        installed_setup = _default_install_dir() / "HoloCore Setup.exe"
        source = installed_setup if installed_setup.is_file() else Path(sys.executable).resolve()
        try:
            stage_native_uninstaller(install_dir=_default_install_dir(), source_setup=source)
        except OSError as exc:
            messagebox.showerror("HoloCore uninstall", str(exc), parent=self.root)
            return
        self.root.destroy()

    def _close(self) -> None:
        if self.applying:
            if not messagebox.askyesno("Close setup?", "HoloCore is still applying your setup. Close anyway?", parent=self.root):
                return
        self.root.destroy()


def launch_setup_app(*, home: str | Path | None = None, project: str | Path | None = None) -> int:
    root = tk.Tk()
    NativeSetupApp(root, home=home, project=project)
    root.mainloop()
    return 0


def launch_uninstall_app(*, install_dir: str | Path, worker_path: str | Path | None = None) -> int:
    root = tk.Tk()
    NativeUninstallApp(root, install_dir=install_dir, worker_path=worker_path)
    root.mainloop()
    return 0


def run_native_ui_smoke_test() -> int:
    """Exercise every native wizard page without writing installation data."""
    root = tk.Tk()
    root.withdraw()
    try:
        app = NativeSetupApp(root, start_discovery=False)
        root.update_idletasks()
        if root.title() != "HoloCore Setup" or len(app.nav_labels) != 4:
            return 1
        root.tk.call("tk", "scaling", 1.5)
        for step in range(4):
            app.show_step(step)
            root.update_idletasks()
            if app.step != step or not app.content.winfo_children():
                return 1
        original_showerror = messagebox.showerror
        try:
            messagebox.showerror = lambda *_args, **_kwargs: None
            app._apply_failed("Release smoke-test retry")
            root.update_idletasks()
        finally:
            messagebox.showerror = original_showerror
        return 0 if app.step == 3 and not app.applying else 1
    finally:
        root.destroy()


def run_native_uninstall_ui_smoke_test() -> int:
    """Render the uninstaller without removing files."""
    root = tk.Tk()
    root.withdraw()
    try:
        app = NativeUninstallApp(root, install_dir=_default_install_dir())
        root.update_idletasks()
        if root.title() != "Uninstall HoloCore" or app.uninstall_button.cget("text") != "Uninstall HoloCore":
            return 1
        app._complete({"uninstalled": True, "data_preserved": True})
        root.update_idletasks()
        return 0 if "data was preserved" in app.status_var.get() else 1
    finally:
        root.destroy()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="HoloCore Setup")
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--home")
    parser.add_argument("--project")
    parser.add_argument("--ui-smoke-test", action="store_true")
    parser.add_argument("--uninstall-ui-smoke-test", action="store_true")
    parser.add_argument("--embedded-install-smoke-test", action="store_true")
    parser.add_argument("--uninstall", action="store_true")
    parser.add_argument("--uninstall-worker", action="store_true")
    parser.add_argument("--install-dir")
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--desktop", action="store_true")
    parser.add_argument("--desktop-ui-smoke-test", action="store_true")
    parser.add_argument("--backup-scheduled", action="store_true")
    args = parser.parse_args(argv)
    if args.backup_scheduled:  # Run by Windows Task Scheduler; no window, outcome recorded for the Control Centre.
        from holocore.home_backup import run_scheduled_backup
        return 0 if run_scheduled_backup(args.home or HomeManager().home)["result"] == "succeeded" else 1
    if args.desktop_ui_smoke_test:
        from holocore.desktop_app import run_desktop_ui_smoke_test
        return run_desktop_ui_smoke_test()
    if args.desktop:
        from holocore.desktop_app import launch_desktop_app
        return launch_desktop_app(project=args.project, home=args.home)
    if args.ui_smoke_test:
        return run_native_ui_smoke_test()
    if args.uninstall_ui_smoke_test:
        return run_native_uninstall_ui_smoke_test()
    if args.embedded_install_smoke_test:
        destination = Path(args.install_dir or (Path(tempfile.gettempdir()) / "holocore-embedded-smoke")).resolve()
        try:
            result = install_embedded_release(install_dir=destination, configure_shell=False)
            required = (destination / "holocore.exe", destination / "HoloCore Setup.exe", destination / "install.ps1")
            return 0 if result.get("installed") and all(path.is_file() for path in required) else 1
        finally:
            if destination.is_dir():
                shutil.rmtree(destination)
    if args.uninstall_worker:
        try:
            if args.quiet:
                uninstall_native_installation(args.install_dir)
                _queue_worker_cleanup(Path(sys.executable).resolve())
                return 0
            return launch_uninstall_app(install_dir=args.install_dir or _default_install_dir(), worker_path=sys.executable)
        except OSError:
            return 1
    if args.uninstall:
        try:
            stage_native_uninstaller(install_dir=args.install_dir, source_setup=sys.executable, quiet=args.quiet)
        except OSError as exc:
            root = tk.Tk()
            root.withdraw()
            messagebox.showerror("HoloCore uninstall", str(exc), parent=root)
            root.destroy()
            return 1
        return 0
    installed_setup = (_default_install_dir() / "HoloCore Setup.exe").resolve()
    if Path(sys.executable).resolve() == installed_setup and (installed_setup.parent / "holocore.exe").is_file():
        from holocore.desktop_app import launch_desktop_app
        return launch_desktop_app()
    try:
        install_embedded_release()
    except OSError as exc:
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("HoloCore installation", f"HoloCore could not install the local engine.\n\n{exc}", parent=root)
        root.destroy()
        return 1
    return launch_setup_app(home=args.home, project=args.project)


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "NativeSetupApp", "NativeUninstallApp", "initial_native_state", "install_embedded_release",
    "launch_setup_app", "launch_uninstall_app", "main", "resource_path",
    "run_native_ui_smoke_test", "run_native_uninstall_ui_smoke_test",
    "stage_native_uninstaller", "uninstall_native_installation",
]
