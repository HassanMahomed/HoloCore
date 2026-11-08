"""Native Windows first-run setup application for HoloCore."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any

from holocore.home import HomeManager
from holocore.setup_wizard import CLIENT_NAMES, apply_setup, build_setup_plan, build_setup_state
from holocore import __version__


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


def _configure_windows_shell(install_dir: Path, setup_executable: Path) -> dict[str, Any]:
    """Add the installed engine to user PATH and expose Setup in the Start menu."""
    if os.name != "nt":
        return {"path_changed": False, "shortcut": None}
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
    programs = Path(os.environ.get("APPDATA", Path.home() / "AppData/Roaming")) / "Microsoft/Windows/Start Menu/Programs"
    shortcut = programs / "HoloCore Setup.lnk"
    quote = lambda value: "'" + str(value).replace("'", "''") + "'"
    script = (
        "$s=(New-Object -ComObject WScript.Shell).CreateShortcut(" + quote(shortcut) + ");"
        "$s.TargetPath=" + quote(setup_executable) + ";"
        "$s.WorkingDirectory=" + quote(install_dir) + ";"
        "$s.Description='Configure HoloCore folders, AI tools, features, and local models';$s.Save()"
    )
    completed = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True, text=True, check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if completed.returncode:
        raise OSError(completed.stderr.strip() or "could not create the HoloCore Setup shortcut")
    return {"path_changed": path_changed, "shortcut": str(shortcut)}


def install_embedded_release(
    *,
    payload_path: str | Path | None = None,
    setup_executable: str | Path | None = None,
    install_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Install the engine carried inside the windowed release executable."""
    payload = Path(payload_path) if payload_path else resource_path("holocore-payload/holocore-windows-x64.exe")
    if not payload.is_file():
        return {"embedded": False, "installed": False}
    source_setup = Path(setup_executable or sys.executable).resolve()
    destination = Path(install_dir or (Path(os.environ.get("LOCALAPPDATA", Path.home())) / "Programs/HoloCore")).resolve()
    engine_target = destination / "holocore.exe"
    setup_target = destination / "HoloCore Setup.exe"
    engine_changed = _copy_if_changed(payload, engine_target)
    setup_changed = False
    if source_setup != setup_target:
        setup_changed = _copy_if_changed(source_setup, setup_target)
    shell = _configure_windows_shell(destination, setup_target)
    return {
        "embedded": True, "installed": True, "install_dir": str(destination),
        "engine": str(engine_target), "setup": str(setup_target),
        "engine_changed": engine_changed, "setup_changed": setup_changed, **shell,
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
        self.plan: dict[str, Any] | None = None
        self.client_details = self.state["clients"]
        self.client_vars = {name: tk.BooleanVar(value=False) for name in CLIENT_NAMES}
        self.home_var = tk.StringVar(value=self.state["home"])
        self.project_var = tk.StringVar(value=self.state["project"])
        self.map_var = tk.BooleanVar(value=True)
        self.update_var = tk.BooleanVar(value=False)
        self.model_var = tk.StringVar(value="")
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
        ttk.Label(nav, text="No cloud account required.\nNo project content is uploaded.", style="NavText.TLabel", justify="left").pack(side="bottom", anchor="w", padx=24, pady=28)

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

    def _show_features(self) -> None:
        body = self._clear_content()
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
        self.next_button.configure(text="Close", state="normal", command=self.root.destroy)

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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="HoloCore Setup")
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--home")
    parser.add_argument("--project")
    parser.add_argument("--ui-smoke-test", action="store_true")
    args = parser.parse_args(argv)
    if args.ui_smoke_test:
        return run_native_ui_smoke_test()
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
    "NativeSetupApp", "initial_native_state", "install_embedded_release",
    "launch_setup_app", "main", "resource_path", "run_native_ui_smoke_test",
]
