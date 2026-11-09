"""Native HoloCore desktop control centre with background service operations."""
from __future__ import annotations

import queue
import threading
import tkinter as tk
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from tkinter import filedialog, messagebox, ttk

from .desktop_service import KNOWLEDGE_SOURCES, DesktopService, default_project
from .home_backup import CATEGORY_LABELS as LABELS
from .setup_app import resource_path

# Copper & ink, taken from the HoloCore logo gradient (ink #1D1B2E → copper #C0521F → amber #F3B33D).
COLORS = {
    "navy": "#1D1B2E", "navy_2": "#2C2940", "nav_text": "#CFC7D6", "nav_muted": "#8E859A",
    "paper": "#F7F2EA", "panel": "#FFFFFF", "ink": "#1D1B2E", "muted": "#6E6577",
    "line": "#E6DCCD", "soft": "#F1E9DD", "accent": "#C0521F", "accent_hover": "#A3441A",
    "amber": "#F3B33D", "select": "#FBE4D3",
}
CARD_ACCENTS = ("#5A2E3F", "#8D3E2C", "#C0521F", "#DA8230", "#F3B33D")  # The logo gradient, left to right.
FONT, BOLD = "Segoe UI", "Segoe UI Semibold"
PAGES = (
    ("home", "Home", "Everything HoloCore holds, at a glance"),
    ("projects", "Projects", "Every project registered in your HoloCore Home"),
    ("knowledge", "Knowledge", "Browse and search notes from this project, your Second Brain, or the whole Home"),
    ("memories", "Memories", "What HoloCore remembered from AI conversations in this project"),
    ("code_map", "Code Map", "The structural map of this project's files, functions, and concepts"),
    ("connections", "AI Connections", "Which AI tools can reach HoloCore, and when each last recorded activity"),
    ("storage", "Storage & Backups", "Memory database size, backups, and restore checks"),
)
ALL_KINDS = "All kinds"
DAY_NAMES = {"MON": "Monday", "TUE": "Tuesday", "WED": "Wednesday", "THU": "Thursday", "FRI": "Friday",
             "SAT": "Saturday", "SUN": "Sunday"}


def clean(text, limit: int | None = None) -> str:
    """Make stored text safe for Tk and optionally fold it onto one short line."""
    value = "".join(ch if ord(ch) <= 0xFFFF else "�" for ch in str(text or ""))
    if limit is not None:
        value = " ".join(value.split())
        if len(value) > limit:
            value = value[: limit - 1] + "…"
    return value


def _local_time(stamp: str) -> datetime:
    return datetime.fromisoformat(stamp).astimezone()


def short_time(stamp: str | None) -> str:
    return f"{_local_time(stamp):%Y-%m-%d %H:%M}" if stamp else "—"


def human_bytes(value: int) -> str:
    size = float(value)
    for unit in ("bytes", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:,.0f} {unit}" if unit == "bytes" else f"{size:,.1f} {unit}"
        size /= 1024
    return f"{value} bytes"


def files(count: int) -> str:
    return f"{count:,} file{'' if count == 1 else 's'}"


def describe_preview(preview: dict) -> str:
    """Plain-language summary of a backup check, shown before anything is restored."""
    if not preview.get("created_at"):
        return "This file can't be used as a HoloCore backup.\n\n" + "\n".join(preview["issues"])
    lines = [f"Backup from {short_time(preview['created_at'])} · HoloCore {preview.get('holocore_version')}",
             f"Made from: {preview.get('source_home')}", ""]
    lines.append("Every file's checksum matches." if preview["valid"] else "Problems found — this backup can't be restored:")
    lines += [f"  • {issue}" for issue in preview["issues"]]
    lines += ["", "Contains:"] + [f"  {value['label']}: {files(value['files'])}, {human_bytes(value['bytes'])}"
                                  for value in preview.get("categories", {}).values()]
    lines += [f"  Projects: {len(preview.get('projects', []))}", "", "Not included: " + "; ".join(preview.get("not_included", []))]
    if preview["warnings"]:
        lines += ["", "Before restoring, note:"] + [f"  • {warning}" for warning in preview["warnings"]]
    lines += ["", "Nothing has been restored."]
    return "\n".join(lines)


def describe_test(result: dict | None) -> str:
    if not result:
        return "Not tested"
    label = {"passed": "Connected", "failed": "Disconnected", "not-configured": "Not configured",
             "invalid-config": "Configuration unreadable"}.get(result["result"], result["result"])
    return f"{label} · tested {_local_time(result['checked_at']):%H:%M:%S}"


def describe_capture(stamp: str | None, now: datetime | None = None) -> str:
    if not stamp:
        return "No capture recorded"
    moment = _local_time(stamp)
    seconds = max(0, int(((now or datetime.now(timezone.utc)) - moment).total_seconds()))
    for size, unit in ((86400, "day"), (3600, "hour"), (60, "minute")):
        if seconds >= size:
            count = seconds // size
            age = f"{count} {unit}{'s' if count != 1 else ''} ago"
            break
    else:
        age = "just now"
    return f"{moment:%Y-%m-%d %H:%M} ({age})"


class DesktopControlCentre:
    def __init__(self, window: tk.Tk, root: str | Path, *, home: str | Path | None = None, autoload: bool = True):
        self.window = window
        self.home = home
        self.service = DesktopService(root, home)
        self.events = queue.Queue()
        self.busy = False
        self.closed = False
        self.loaded: set[str] = set()
        self.page = "home"
        self.action_buttons: list[ttk.Button] = []
        self.project_rows: list[dict] = []
        self.connection_rows: list[dict] = []
        self.test_results: dict[str, dict] = {}
        self.note_rows: dict[str, dict] = {}
        self.memory_rows: dict[str, dict] = {}
        self.schedule: dict | None = None
        window.title("HoloCore — Control Centre")
        self.scale = max(1.0, window.winfo_fpixels("1i") / 96)
        window.geometry(f"{self.px(1240)}x{self.px(800)}")
        window.minsize(self.px(1000), self.px(640))
        window.configure(bg=COLORS["paper"])
        window.protocol("WM_DELETE_WINDOW", self.close)
        self._styles()
        window.columnconfigure(1, weight=1)
        window.rowconfigure(0, weight=1)
        self._sidebar()
        self._main()
        self.pages: dict[str, ttk.Frame] = {}
        for key, _label, _subtitle in PAGES:
            frame = ttk.Frame(self.body)
            frame.grid(row=0, column=0, sticky="nsew")
            self.pages[key] = frame
            getattr(self, f"_build_{key}")(frame)
        self.show_page("home")
        self.poll_id = window.after(75, self.poll)
        if autoload:
            self.refresh()

    # Layout helpers ----------------------------------------------------------
    def _styles(self) -> None:
        c = COLORS
        style = ttk.Style(self.window)
        style.theme_use("clam")
        style.configure("TFrame", background=c["paper"])
        style.configure("TLabel", background=c["paper"], foreground=c["ink"], font=(FONT, 10))
        style.configure("Muted.TLabel", background=c["paper"], foreground=c["muted"], font=(FONT, 9))
        style.configure("Section.TLabel", background=c["paper"], foreground=c["ink"], font=(BOLD, 12))
        style.configure("TEntry", fieldbackground="#FFFFFF", foreground=c["ink"], bordercolor=c["line"], padding=7)
        style.configure("TCombobox", fieldbackground="#FFFFFF", foreground=c["ink"], bordercolor=c["line"], padding=6)
        style.map("TCombobox", fieldbackground=[("readonly", "#FFFFFF")], foreground=[("readonly", c["ink"])])
        style.configure("Primary.TButton", background=c["accent"], foreground="#FFFFFF", bordercolor=c["accent"],
                        lightcolor=c["accent"], darkcolor=c["accent"], font=(BOLD, 10), padding=(16, 8))
        style.map("Primary.TButton", background=[("active", c["accent_hover"]), ("disabled", "#C9B8A8")])
        style.configure("Secondary.TButton", background="#FFFFFF", foreground=c["ink"], bordercolor=c["line"],
                        lightcolor="#FFFFFF", darkcolor="#FFFFFF", font=(BOLD, 10), padding=(14, 8))
        style.map("Secondary.TButton", background=[("active", c["soft"])], foreground=[("disabled", "#B5AAA0")])
        style.configure("NavAction.TButton", background=c["navy_2"], foreground="#FFFFFF", bordercolor=c["navy_2"],
                        lightcolor=c["navy_2"], darkcolor=c["navy_2"], font=(BOLD, 9), padding=(12, 8))
        style.map("NavAction.TButton", background=[("active", c["accent"]), ("disabled", c["navy_2"])])
        style.configure("TCheckbutton", background=c["paper"], foreground=c["ink"], font=(FONT, 10),
                        indicatorsize=self.px(14), indicatorbackground="#FFFFFF", indicatormargin=(0, 0, self.px(6), 0))
        style.map("TCheckbutton", background=[("active", c["paper"])],
                  indicatorbackground=[("selected", c["accent"])], indicatorforeground=[("selected", "#FFFFFF")])
        style.configure("Treeview", background=c["panel"], fieldbackground=c["panel"], foreground=c["ink"],
                        rowheight=self.px(30), font=(FONT, 10), borderwidth=0)
        style.configure("Treeview.Heading", background=c["soft"], foreground=c["muted"], font=(BOLD, 9),
                        relief="flat", padding=(8, 6))
        style.map("Treeview.Heading", background=[("active", c["line"])])
        style.map("Treeview", background=[("selected", c["select"])], foreground=[("selected", c["ink"])])
        style.configure("TPanedwindow", background=c["paper"])
        style.configure("Vertical.TScrollbar", background=c["soft"], troughcolor=c["panel"], bordercolor=c["panel"],
                        arrowcolor=c["muted"], lightcolor=c["soft"], darkcolor=c["soft"])

    def px(self, value: int) -> int:
        """Scale a 96-DPI pixel size to the display, since the window is DPI-aware."""
        return round(value * self.scale)

    def _logo(self, small: bool) -> tk.PhotoImage | None:
        size = 96 if self.scale >= 1.5 and not small else 48
        for path in (resource_path(f"holocore-assets/holocore-icon-{size}.png"),
                     resource_path(f"docs/assets/holocore-icon-{size}.png")):
            if path.is_file():
                try:
                    image = tk.PhotoImage(master=self.window, file=str(path))
                except tk.TclError:
                    return None
                return image.subsample(2, 2) if small and size == 96 else image
        return None

    def _sidebar(self) -> None:
        c = COLORS
        nav = tk.Frame(self.window, bg=c["navy"], width=self.px(236))
        nav.grid(row=0, column=0, sticky="nsw")
        nav.pack_propagate(False)
        brand = tk.Frame(nav, bg=c["navy"])
        brand.pack(fill="x", padx=self.px(22), pady=(self.px(26), self.px(28)))
        self.logo_image = self._logo(small=False)
        if self.logo_image is not None:
            self.window.iconphoto(True, self.logo_image)
            tk.Label(brand, image=self.logo_image, bg=c["navy"]).pack(side="left", padx=(0, self.px(12)))
        words = tk.Frame(brand, bg=c["navy"])
        words.pack(side="left", fill="x")
        tk.Label(words, text="HoloCore", bg=c["navy"], fg="#FFFFFF", font=(BOLD, 16)).pack(anchor="w")
        tk.Label(words, text="Control Centre", bg=c["navy"], fg=c["amber"], font=(FONT, 9)).pack(anchor="w")
        self.nav_items: dict[str, tuple[tk.Frame, tk.Frame, tk.Label]] = {}
        for key, label, _subtitle in PAGES:
            row = tk.Frame(nav, bg=c["navy"], cursor="hand2")
            row.pack(fill="x")
            bar = tk.Frame(row, bg=c["navy"], width=self.px(4))
            bar.pack(side="left", fill="y")
            text = tk.Label(row, text=label, bg=c["navy"], fg=c["nav_text"], font=(FONT, 10), anchor="w",
                            padx=self.px(20), pady=self.px(10), cursor="hand2")
            text.pack(side="left", fill="x", expand=True)
            for widget in (row, text):
                widget.bind("<Button-1>", lambda _event, page=key: self.show_page(page))
                widget.bind("<Enter>", lambda _event, page=key: self._hover(page, True))
                widget.bind("<Leave>", lambda _event, page=key: self._hover(page, False))
            self.nav_items[key] = (row, bar, text)
        self.refresh_button = ttk.Button(nav, text="Refresh", style="NavAction.TButton", command=self.refresh)
        self.refresh_button.pack(side="bottom", fill="x", padx=self.px(22), pady=(self.px(10), self.px(24)))
        self.action_buttons.append(self.refresh_button)
        self.project_label = tk.Label(nav, text=self.service.world, bg=c["navy"], fg="#FFFFFF", font=(BOLD, 11),
                                      wraplength=self.px(190), justify="left", anchor="w")
        self.project_label.pack(side="bottom", fill="x", padx=self.px(22))
        tk.Label(nav, text="CURRENT PROJECT", bg=c["navy"], fg=c["nav_muted"], font=(BOLD, 8)).pack(
            side="bottom", anchor="w", padx=self.px(22), pady=(0, self.px(4)))

    def _paint_nav(self, page: str, active: bool, hover: bool = False) -> None:
        row, bar, text = self.nav_items[page]
        background = COLORS["navy_2"] if active or hover else COLORS["navy"]
        row.configure(bg=background)
        bar.configure(bg=COLORS["accent"] if active else background)
        text.configure(bg=background, fg="#FFFFFF" if active else COLORS["nav_text"], font=(BOLD if active else FONT, 10))

    def _hover(self, page: str, inside: bool) -> None:
        if page != self.page:
            self._paint_nav(page, False, inside)

    def _main(self) -> None:
        c = COLORS
        main = ttk.Frame(self.window)
        main.grid(row=0, column=1, sticky="nsew")
        main.columnconfigure(0, weight=1)
        main.rowconfigure(2, weight=1)
        header = tk.Frame(main, bg=c["panel"], padx=self.px(32), pady=self.px(20))
        header.grid(row=0, column=0, sticky="ew")
        self.title_var, self.subtitle_var = tk.StringVar(), tk.StringVar()
        tk.Label(header, textvariable=self.title_var, bg=c["panel"], fg=c["ink"], font=(BOLD, 20)).pack(anchor="w")
        tk.Label(header, textvariable=self.subtitle_var, bg=c["panel"], fg=c["muted"], font=(FONT, 10)).pack(anchor="w")
        tk.Frame(main, bg=c["line"], height=1).grid(row=1, column=0, sticky="ew")
        self.body = ttk.Frame(main, padding=(self.px(32), self.px(24)))
        self.body.grid(row=2, column=0, sticky="nsew")
        self.body.columnconfigure(0, weight=1)
        self.body.rowconfigure(0, weight=1)
        bar = tk.Frame(main, bg=c["soft"], padx=self.px(32), pady=self.px(6))
        bar.grid(row=3, column=0, sticky="ew")
        self.status = tk.StringVar(value="Ready")
        tk.Label(bar, textvariable=self.status, bg=c["soft"], fg=c["muted"], font=(FONT, 9), anchor="w").pack(fill="x")

    def button(self, parent, text, command, primary=False) -> ttk.Button:
        widget = ttk.Button(parent, text=text, command=command, style="Primary.TButton" if primary else "Secondary.TButton")
        self.action_buttons.append(widget)
        return widget

    def card(self, parent, title: str, column: int) -> tuple[tk.StringVar, tk.StringVar]:
        c = COLORS
        outer = tk.Frame(parent, bg=c["line"])
        outer.grid(row=0, column=column, sticky="nsew", padx=(0 if column == 0 else self.px(12), 0))
        tk.Frame(outer, bg=CARD_ACCENTS[column % len(CARD_ACCENTS)], height=self.px(3)).pack(fill="x")
        frame = tk.Frame(outer, bg=c["panel"], padx=self.px(18), pady=self.px(14))
        frame.pack(fill="both", expand=True, padx=1, pady=(0, 1))
        parent.columnconfigure(column, weight=1, uniform="cards")
        value, note = tk.StringVar(value="—"), tk.StringVar(value="")
        tk.Label(frame, text=title.upper(), bg=c["panel"], fg=c["muted"], font=(BOLD, 8)).pack(anchor="w")
        tk.Label(frame, textvariable=value, bg=c["panel"], fg=c["ink"], font=(BOLD, 22)).pack(anchor="w", pady=(2, 0))
        caption = tk.Label(frame, textvariable=note, bg=c["panel"], fg=c["muted"], font=(FONT, 9), wraplength=160,
                           justify="left", anchor="w")
        caption.pack(fill="x")
        # Wrap to the card's real width so captions are never clipped at the edges.
        frame.bind("<Configure>", lambda event: caption.configure(wraplength=max(80, event.width - self.px(40))))
        return value, note

    def cards(self, parent, titles: tuple[tuple[str, str], ...]) -> dict[str, tuple[tk.StringVar, tk.StringVar]]:
        row = ttk.Frame(parent)
        row.pack(fill="x")
        return {key: self.card(row, title, index) for index, (key, title) in enumerate(titles)}

    def table(self, parent, columns: tuple[tuple[str, str, int], ...], height: int = 10) -> tuple[tk.Frame, ttk.Treeview]:
        frame = tk.Frame(parent, bg=COLORS["panel"], highlightbackground=COLORS["line"], highlightthickness=1)
        tree = ttk.Treeview(frame, columns=[key for key, _label, _width in columns], show="headings",
                            height=height, selectmode="browse")
        for key, label, width in columns:
            tree.heading(key, text=label, anchor="w")
            tree.column(key, width=self.px(width), minwidth=self.px(60), anchor="w")
        tree.tag_configure("current", font=(BOLD, 10))
        tree.tag_configure("muted", foreground=COLORS["muted"])
        scrollbar = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        tree.pack(fill="both", expand=True)
        return frame, tree

    @staticmethod
    def preview(parent) -> tuple[tk.Frame, tk.Text]:
        frame = tk.Frame(parent, bg=COLORS["panel"], highlightbackground=COLORS["line"], highlightthickness=1)
        scrollbar = ttk.Scrollbar(frame)
        scrollbar.pack(side="right", fill="y")
        text = tk.Text(frame, wrap="word", width=40, height=8, font=(FONT, 10), bg=COLORS["panel"], fg=COLORS["ink"], relief="flat",
                       borderwidth=0, padx=18, pady=14, state="disabled", yscrollcommand=scrollbar.set)
        text.pack(fill="both", expand=True)
        scrollbar.configure(command=text.yview)
        return frame, text

    @staticmethod
    def set_text(widget: tk.Text, value: str) -> None:
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", clean(value))
        widget.configure(state="disabled")

    @staticmethod
    def fill(tree: ttk.Treeview, rows) -> None:
        tree.delete(*tree.get_children())
        for iid, values, tags in rows:
            tree.insert("", "end", iid=iid, values=[clean(value) for value in values], tags=tags)

    def toolbar(self, parent) -> ttk.Frame:
        bar = ttk.Frame(parent)
        bar.pack(fill="x", pady=(0, 8))
        return bar

    # Pages -------------------------------------------------------------------
    def _build_home(self, page) -> None:
        self.home_cards = self.cards(page, (("projects", "Projects"), ("knowledge", "Knowledge notes"),
                                            ("second_brain", "Second Brain notes"), ("memories", "Memories"),
                                            ("connections", "AI tools configured")))
        lower = ttk.Frame(page)
        lower.pack(fill="both", expand=True, pady=(24, 0))
        lower.columnconfigure(0, weight=3)
        lower.columnconfigure(1, weight=2)
        lower.rowconfigure(1, weight=1)
        self.activity_heading = ttk.Label(lower, text="Recent activity", style="Section.TLabel")
        self.activity_heading.grid(row=0, column=0, sticky="w", pady=(0, 8))
        ttk.Label(lower, text="AI connections", style="Section.TLabel").grid(row=0, column=1, sticky="w", padx=(16, 0), pady=(0, 8))
        frame, self.activity_table = self.table(lower, (("when", "When", 130), ("what", "What happened", 440)))
        frame.grid(row=1, column=0, sticky="nsew")
        frame, self.home_connection_table = self.table(lower, (("tool", "AI tool", 100), ("configuration", "Configuration", 120),
                                                               ("capture", "Last capture", 210)))
        frame.grid(row=1, column=1, sticky="nsew", padx=(16, 0))

    def _build_projects(self, page) -> None:
        bar = self.toolbar(page)
        self.button(bar, "Open selected project", self.open_selected_project, primary=True).pack(side="left")
        self.button(bar, "Choose another folder…", self.choose_project).pack(side="left", padx=8)
        ttk.Label(bar, text="Register new projects and connect AI tools with HoloCore Setup.", style="Muted.TLabel").pack(side="left", padx=8)
        frame, self.project_table = self.table(page, (("name", "Project", 230), ("notes", "Knowledge notes", 120),
                                                      ("memories", "Memories", 100), ("last", "Last activity", 150),
                                                      ("folder", "Project folder", 420)), height=16)
        frame.pack(fill="both", expand=True)
        self.project_table.bind("<Double-1>", lambda _event: self.open_selected_project())

    def _build_knowledge(self, page) -> None:
        bar = self.toolbar(page)
        ttk.Label(bar, text="Source").pack(side="left")
        self.source = ttk.Combobox(bar, state="readonly", width=16, values=list(KNOWLEDGE_SOURCES.values()))
        self.source.current(0)
        self.source.pack(side="left", padx=(8, 16))
        self.source.bind("<<ComboboxSelected>>", lambda _event: self.reload("knowledge"))
        self.knowledge_query = ttk.Entry(bar, width=36)
        self.knowledge_query.pack(side="left")
        self.knowledge_query.bind("<Return>", lambda _event: self.search_notes())
        self.button(bar, "Search", self.search_notes, primary=True).pack(side="left", padx=8)
        self.button(bar, "Show recent", lambda: self.reload("knowledge")).pack(side="left")
        self.knowledge_count = ttk.Label(page, style="Muted.TLabel")
        self.knowledge_count.pack(anchor="w", pady=(0, 8))
        split = ttk.PanedWindow(page, orient="horizontal")
        split.pack(fill="both", expand=True)
        frame, self.note_table = self.table(split, (("title", "Note", 340), ("folder", "Folder", 150), ("modified", "Modified", 130)))
        split.add(frame, weight=3)
        frame, self.note_preview = self.preview(split)
        split.add(frame, weight=2)
        self.note_table.bind("<<TreeviewSelect>>", lambda _event: self.show_note())

    def _build_memories(self, page) -> None:
        bar = self.toolbar(page)
        ttk.Label(bar, text="Kind").pack(side="left")
        self.memory_kind = ttk.Combobox(bar, state="readonly", width=14, values=[ALL_KINDS])
        self.memory_kind.current(0)
        self.memory_kind.pack(side="left", padx=(8, 16))
        self.memory_kind.bind("<<ComboboxSelected>>", lambda _event: self.reload("memories"))
        self.memory_query = ttk.Entry(bar, width=36)
        self.memory_query.pack(side="left")
        self.memory_query.bind("<Return>", lambda _event: self.reload("memories"))
        self.button(bar, "Search", lambda: self.reload("memories"), primary=True).pack(side="left", padx=8)
        self.hide_noise = tk.BooleanVar(value=True)
        ttk.Checkbutton(bar, text="Hide short and noisy memories", variable=self.hide_noise,
                        command=lambda: self.reload("memories")).pack(side="left", padx=8)
        self.memory_count = ttk.Label(page, style="Muted.TLabel")
        self.memory_count.pack(anchor="w", pady=(0, 8))
        split = ttk.PanedWindow(page, orient="horizontal")
        split.pack(fill="both", expand=True)
        frame, self.memory_table = self.table(split, (("when", "When", 130), ("kind", "Kind", 90), ("memory", "Memory", 460)))
        split.add(frame, weight=3)
        frame, self.memory_preview = self.preview(split)
        split.add(frame, weight=2)
        self.memory_table.bind("<<TreeviewSelect>>", lambda _event: self.show_memory())

    def _build_code_map(self, page) -> None:
        self.code_cards = self.cards(page, (("signals", "Signals"), ("links", "Relationships"),
                                            ("files", "Files mapped"), ("updated", "Last refreshed")))
        lower = ttk.Frame(page)
        lower.pack(fill="both", expand=True, pady=(24, 0))
        lower.columnconfigure(0, weight=1)
        lower.columnconfigure(1, weight=3)
        lower.rowconfigure(1, weight=1)
        ttk.Label(lower, text="Signals by kind", style="Section.TLabel").grid(row=0, column=0, sticky="w", pady=(0, 8))
        ttk.Label(lower, text="Most connected code — files, classes, and functions", style="Section.TLabel").grid(
            row=0, column=1, sticky="w", padx=(16, 0), pady=(0, 8))
        frame, self.kind_table = self.table(lower, (("kind", "Kind", 150), ("count", "Signals", 80)))
        frame.grid(row=1, column=0, sticky="nsew")
        frame, self.connected_table = self.table(lower, (("label", "Name", 260), ("kind", "Kind", 110),
                                                         ("links", "Links", 70), ("source", "Source file", 320)))
        frame.grid(row=1, column=1, sticky="nsew", padx=(16, 0))

    def _build_connections(self, page) -> None:
        ttk.Label(page, text="Test starts the HoloCore command each AI tool is configured to run. It does not change the tool's "
                  "settings, and a passing test does not prove capture is working: check Last capture for that.",
                  style="Muted.TLabel", wraplength=900, justify="left").pack(anchor="w", pady=(0, 12))
        frame, self.connection_table = self.table(page, (("tool", "AI tool", 120), ("configuration", "Configuration", 170),
                                                         ("test", "Connection test", 260), ("capture", "Last capture", 260)), height=6)
        frame.pack(fill="x")
        self.connection_table.bind("<<TreeviewSelect>>", lambda _event: self.show_connection_detail())
        self.test_button = self.button(page, "Test selected connection", self.test_connection, primary=True)
        self.test_button.pack(anchor="w", pady=12)
        frame, self.connection_text = self.preview(page)
        frame.pack(fill="both", expand=True)

    def _build_storage(self, page) -> None:
        self.storage_cards = self.cards(page, (("database", "Memories database"), ("journal", "Pending journal"),
                                               ("total", "Total on disk"), ("last_backup", "Last scheduled backup")))
        ttk.Label(page, text="Back up and restore", style="Section.TLabel").pack(anchor="w", pady=(22, 4))
        ttk.Label(page, text="A backup is one .zip of everything HoloCore cannot rebuild: knowledge notes, memories, "
                  "original chat transcripts, project sources, and the project registry. Copy it to another computer "
                  "to move your HoloCore. Code maps are rebuilt automatically and are left out.",
                  style="Muted.TLabel", wraplength=self.px(960), justify="left").pack(anchor="w", pady=(0, 8))
        bar = ttk.Frame(page)
        bar.pack(fill="x")
        self.backup_button = self.button(bar, "Back up now…", self.backup, primary=True)
        self.backup_button.pack(side="left")
        self.preview_button = self.button(bar, "Check a backup…", self.restore_preview)
        self.preview_button.pack(side="left", padx=8)
        self.restore_button = self.button(bar, "Restore from backup…", self.restore)
        self.restore_button.pack(side="left")
        ttk.Label(page, text="Scheduled backups", style="Section.TLabel").pack(anchor="w", pady=(22, 4))
        self.schedule_label = ttk.Label(page, text="", style="Muted.TLabel", wraplength=self.px(960), justify="left")
        self.schedule_label.pack(anchor="w", pady=(0, 8))
        bar = ttk.Frame(page)
        bar.pack(fill="x", pady=(0, 16))
        self.button(bar, "Set up schedule…", self.edit_schedule, primary=True).pack(side="left")
        # Not in action_buttons: its state follows whether a schedule is on, not just whether HoloCore is busy.
        self.schedule_off_button = ttk.Button(bar, text="Turn off", style="Secondary.TButton", command=self.stop_schedule,
                                              state="disabled")
        self.schedule_off_button.pack(side="left", padx=8)
        frame, self.storage_text = self.preview(page)
        frame.pack(fill="both", expand=True)

    # Navigation and background work ------------------------------------------
    def show_page(self, page: str) -> None:
        self.page = page
        for key in self.nav_items:
            self._paint_nav(key, key == page)
        _key, title, subtitle = next(row for row in PAGES if row[0] == page)
        self.title_var.set(title)
        self.subtitle_var.set(subtitle)
        self.pages[page].tkraise()
        self.ensure_loaded()

    def ensure_loaded(self) -> None:
        """Load the Home summary first, then whichever data the visible page needs."""
        if self.busy or self.closed:
            return
        if "dashboard" not in self.loaded:
            self.loaded.add("dashboard")
            self.run("Loading your HoloCore Home…", self.service.dashboard, self.show_dashboard)
            return
        loader = {"knowledge": self.load_knowledge, "memories": self.load_memories,
                  "code_map": self.load_code_map}.get(self.page)
        if loader and self.page not in self.loaded:
            self.loaded.add(self.page)
            loader()

    def reload(self, page: str) -> None:
        if not self.busy:
            self.loaded.discard(page)
            self.ensure_loaded()

    def run(self, label, operation, callback) -> None:
        if self.busy or self.closed:
            return
        self.busy = True
        self.status.set(label)
        self.set_busy(True)

        def work():
            try:
                self.events.put((callback, operation(), None))
            except Exception as exc:
                self.events.put((callback, None, str(exc)))
        threading.Thread(target=work, daemon=True).start()

    def set_busy(self, busy: bool) -> None:
        for widget in self.action_buttons:
            widget.configure(state="disabled" if busy else "normal")

    def poll(self) -> None:
        if self.closed:
            return
        try:
            callback, result, error = self.events.get_nowait()
        except queue.Empty:
            pass
        else:
            self.busy = False
            self.set_busy(False)
            if error:
                self.status.set("Needs attention: " + error)
            else:
                try:
                    callback(result)
                except Exception as exc:
                    self.status.set("Could not display results: " + str(exc))
                else:
                    self.status.set("Ready")
            self.ensure_loaded()
        self.poll_id = self.window.after(75, self.poll)

    def refresh(self) -> None:
        if not self.busy:
            self.loaded.clear()
            self.ensure_loaded()

    def close(self) -> None:
        self.closed = True
        self.window.after_cancel(self.poll_id)
        self.window.destroy()

    # Home, projects, connections, storage ------------------------------------
    def show_dashboard(self, result: dict) -> None:
        summary = result["summary"]
        self.project_rows = summary["projects"]
        world = self.service.world
        current = next((row for row in self.project_rows if row["id"] == world), None)
        self.project_label.configure(text=world)
        cards = self.home_cards
        cards["projects"][0].set(f"{len(self.project_rows):,}")
        cards["projects"][1].set(f"{sum(1 for row in self.project_rows if row['available']):,} with a folder on this computer")
        cards["knowledge"][0].set(f"{summary['knowledge_notes']:,}")
        cards["knowledge"][1].set(f"{current['notes'] if current else 0:,} in {world}")
        cards["second_brain"][0].set(f"{summary['second_brain_notes']:,}")
        cards["second_brain"][1].set("Curated notes in the Second Brain project")
        cards["memories"][0].set(f"{summary['memories']:,}")
        cards["memories"][1].set(f"{summary['project_memories']:,} in {world}")
        self.connection_rows = result["connections"]
        configured = sum(1 for row in self.connection_rows if row["configured"])
        cards["connections"][0].set(f"{configured} of {len(self.connection_rows)}")
        latest = max((row["last_capture"] for row in self.connection_rows if row["last_capture"]), default=None)
        cards["connections"][1].set(f"Last capture {describe_capture(latest)}" if latest else "No capture recorded for this project")
        self.activity_heading.configure(text=f"Recent activity in {world}")
        self.fill(self.activity_table, ((str(i), (short_time(row["occurred_at"]), clean(row["label"], 140)), ())
                                        for i, row in enumerate(result["timeline"])))
        self.fill(self.home_connection_table, ((row["name"], (row["name"].title(), row["configuration"],
                                                              describe_capture(row["last_capture"])), ())
                                               for row in self.connection_rows))
        self.show_projects()
        self.show_connections()
        self.show_storage(result["storage"], result["database"])
        self.show_schedule(result.get("backups"))

    def show_projects(self) -> None:
        world = self.service.world
        self.fill(self.project_table, ((str(i), (row["name"], f"{row['notes']:,}", f"{row['memories']:,}",
                                                 short_time(row["last_activity"]),
                                                 row["root"] if row["available"] else row["root"] + "  (folder missing)"),
                                        ("current",) if row["id"] == world else () if row["available"] else ("muted",))
                                       for i, row in enumerate(self.project_rows)))

    def open_selected_project(self) -> None:
        selected = self.project_table.selection()
        if selected:
            self.switch(self.project_rows[int(selected[0])]["root"])

    def choose_project(self) -> None:
        folder = filedialog.askdirectory(parent=self.window, title="Choose project folder")
        if folder:
            self.switch(folder)

    def switch(self, root) -> None:
        if self.busy:
            return
        try:
            self.service = DesktopService(root, self.home)
        except Exception as exc:
            messagebox.showerror("Could not open project", str(exc), parent=self.window)
            return
        self.project_label.configure(text=self.service.world)
        for tree in (self.note_table, self.memory_table):
            tree.delete(*tree.get_children())
        self.set_text(self.note_preview, "")
        self.set_text(self.memory_preview, "")
        self.memory_kind.configure(values=[ALL_KINDS])
        self.memory_kind.current(0)
        self.refresh()

    def show_connections(self) -> None:
        selected = self.connection_table.selection()
        self.fill(self.connection_table, ((row["name"], (row["name"].title(), row["configuration"],
                                                         describe_test(self.test_results.get(row["name"])),
                                                         describe_capture(row["last_capture"])), ())
                                          for row in self.connection_rows))
        if selected and self.connection_table.exists(selected[0]):
            self.connection_table.selection_set(selected[0])
        elif self.connection_rows:
            self.connection_table.selection_set(self.connection_rows[0]["name"])
        self.show_connection_detail()

    def selected_connection(self) -> dict | None:
        selected = self.connection_table.selection()
        return next((row for row in self.connection_rows if selected and row["name"] == selected[0]), None)

    def show_connection_detail(self) -> None:
        row = self.selected_connection()
        if row is None:
            self.set_text(self.connection_text, "")
            return
        test = self.test_results.get(row["name"])
        lines = [row["name"].title(), "", f"Configuration: {row['configuration']}", row["detail"], row["config_path"], "",
                 f"Connection test: {describe_test(test)}"]
        if test:
            lines.append(test["detail"])
        lines += ["", f"Last capture: {describe_capture(row['last_capture'])}",
                  "Last capture is the latest activity HoloCore recorded from this tool for this project."]
        self.set_text(self.connection_text, "\n".join(lines))

    def test_connection(self) -> None:
        row = self.selected_connection()
        if row is None:
            return
        name = row["name"]

        def store(result):
            self.test_results[name] = result
            self.show_connections()
        self.run(f"Testing {name.title()} connection…", lambda: self.service.test_connection(name), store)

    def show_storage(self, storage: dict, database: str) -> None:
        cards = self.storage_cards
        if not storage.get("exists"):
            for value, note in cards.values():
                value.set("—")
                note.set("")
            self.set_text(self.storage_text, f"No memory database exists yet.\n\n{database}")
            return
        cards["database"][0].set(human_bytes(storage["database_bytes"]))
        cards["database"][1].set(f"{storage['page_count']:,} pages · {storage['journal_mode'].upper()} mode")
        cards["journal"][0].set(human_bytes(storage["wal_bytes"]))
        cards["journal"][1].set("Committed changes not yet merged into the database file")
        cards["total"][0].set(human_bytes(storage["total_bytes"]))
        cards["total"][1].set("Database plus journal files")
        self.set_text(self.storage_text, f"Memory database\n{database}")

    def show_schedule(self, settings: dict | None) -> None:
        self.schedule = settings
        value, note = self.storage_cards["last_backup"]
        if not settings:
            self.schedule_label.configure(text="Scheduled backups need a project that belongs to a HoloCore Home.")
            value.set("—")
            note.set("")
            return
        when = (f"every {DAY_NAMES[settings['day']]}" if settings["frequency"] == "weekly" else "every day") + f" at {settings['time']}"
        self.schedule_label.configure(text=(
            f"On: {when}, keeping the newest {settings['keep']} in {settings['folder']}" if settings["enabled"]
            else "Off. Set up a schedule to back up automatically; nothing runs until you choose a folder."))
        self.schedule_off_button.configure(state="normal" if settings["enabled"] else "disabled")
        run = settings.get("last_run")
        if not run:
            value.set("Never")
            note.set("No scheduled backup has run yet")
        elif run["result"] == "succeeded":
            value.set(describe_capture(run["finished_at"]).split(" (")[-1].rstrip(")"))
            note.set(f"Succeeded {short_time(run['finished_at'])} · {human_bytes(run['bytes'])}")
        else:
            value.set("Failed")
            note.set(f"{short_time(run['finished_at'])}: {clean(run.get('error'), 90)}")

    def backup(self) -> None:
        directory = filedialog.askdirectory(parent=self.window, title="Choose where to save the backup")
        if directory:
            self.run("Backing up your HoloCore Home…", lambda: self.service.backup(directory), self.show_backup)

    def show_backup(self, result: dict) -> None:
        lines = ["Backup created and every file checksummed", "", result["backup"], f"Size: {human_bytes(result['bytes'])}", ""]
        lines += [f"{LABELS.get(key, key)}: {files(value['files'])}, {human_bytes(value['bytes'])}"
                  for key, value in result["categories"].items()]
        if result["skipped"]:
            lines += ["", f"{len(result['skipped'])} file(s) were in use and skipped:"]
            lines += [f"  {row['path']}" for row in result["skipped"][:10]]
        self.set_text(self.storage_text, "\n".join(lines))

    def choose_backup_file(self, title: str) -> str:
        return filedialog.askopenfilename(parent=self.window, title=title,
                                          filetypes=[("HoloCore backups", "*.zip"), ("All files", "*.*")])

    def restore_preview(self) -> None:
        backup = self.choose_backup_file("Choose a HoloCore backup to check")
        if backup:
            self.run("Checking every file in the backup…", lambda: self.service.restore_preview(backup),
                     lambda result: self.set_text(self.storage_text, describe_preview(result)))

    def restore(self) -> None:
        backup = self.choose_backup_file("Choose a HoloCore backup to restore")
        if backup:
            self.run("Checking every file in the backup…", lambda: self.service.restore_preview(backup), self.confirm_restore)

    def confirm_restore(self, preview: dict) -> None:
        self.set_text(self.storage_text, describe_preview(preview))
        if not preview["valid"]:
            messagebox.showerror("This backup can't be restored", "\n".join(preview["issues"][:8]), parent=self.window)
            return
        message = (f"Restore the backup from {short_time(preview['created_at'])}?\n\n"
                   "This replaces this Home's knowledge notes, memories, original chat transcripts, project sources, "
                   "and project registry with the backup's copies.\n\nA safety backup of everything as it is now is "
                   f"saved first, in:\n{Path(preview['destination']) / 'Restore Safety Backups'}")
        if preview["warnings"]:
            message += "\n\n" + "\n\n".join(preview["warnings"])
        if messagebox.askyesno("Restore from backup", message, icon="warning", default="no", parent=self.window):
            backup = preview["backup"]
            self.run("Restoring… HoloCore keeps working while this runs", lambda: self.service.restore(backup), self.show_restored)

    def show_restored(self, result: dict) -> None:
        self.set_text(self.storage_text, f"Restored the backup from {short_time(result['created_at'])}.\n\n"
                                         f"Safety backup of the previous state:\n{result['safety_backup']}")
        self.refresh()

    def edit_schedule(self) -> None:
        settings = dict(self.schedule or {"folder": None, "frequency": "weekly", "day": "SUN", "time": "20:00", "keep": 4})
        dialog = tk.Toplevel(self.window)
        dialog.title("Scheduled backups")
        dialog.configure(bg=COLORS["paper"], padx=self.px(24), pady=self.px(20))
        dialog.transient(self.window)
        dialog.resizable(False, False)
        folder = tk.StringVar(value=settings.get("folder") or "")
        frequency = tk.StringVar(value=settings["frequency"])
        day = tk.StringVar(value=DAY_NAMES[settings["day"]])
        time_value = tk.StringVar(value=settings["time"])
        keep = tk.StringVar(value=str(settings["keep"]))
        ttk.Label(dialog, text="Backup folder").grid(row=0, column=0, sticky="w", pady=6)
        ttk.Entry(dialog, textvariable=folder, width=48).grid(row=0, column=1, sticky="ew", padx=8)
        ttk.Button(dialog, text="Browse…", style="Secondary.TButton", command=lambda: folder.set(
            filedialog.askdirectory(parent=dialog, title="Choose a folder for scheduled backups") or folder.get())).grid(row=0, column=2)
        ttk.Label(dialog, text="How often").grid(row=1, column=0, sticky="w", pady=6)
        often = ttk.Frame(dialog)
        often.grid(row=1, column=1, sticky="w", padx=8)
        for text, value_ in (("Weekly", "weekly"), ("Daily", "daily")):
            ttk.Radiobutton(often, text=text, value=value_, variable=frequency).pack(side="left", padx=(0, 12))
        ttk.Label(dialog, text="Day").grid(row=2, column=0, sticky="w", pady=6)
        ttk.Combobox(dialog, textvariable=day, values=list(DAY_NAMES.values()), state="readonly", width=12).grid(
            row=2, column=1, sticky="w", padx=8)
        ttk.Label(dialog, text="Time (24-hour)").grid(row=3, column=0, sticky="w", pady=6)
        ttk.Entry(dialog, textvariable=time_value, width=8).grid(row=3, column=1, sticky="w", padx=8)
        ttk.Label(dialog, text="Backups to keep").grid(row=4, column=0, sticky="w", pady=6)
        ttk.Spinbox(dialog, from_=1, to=52, textvariable=keep, width=6).grid(row=4, column=1, sticky="w", padx=8)
        ttk.Label(dialog, text="Runs from Windows Task Scheduler without opening a window. Older scheduled backups "
                  "beyond the number to keep are deleted from this folder.", style="Muted.TLabel",
                  wraplength=self.px(460), justify="left").grid(row=5, column=0, columnspan=3, sticky="w", pady=(10, 14))
        actions = ttk.Frame(dialog)
        actions.grid(row=6, column=0, columnspan=3, sticky="e")

        def save():
            if not folder.get().strip():
                messagebox.showerror("Scheduled backups", "Choose a backup folder first.", parent=dialog)
                return
            values = {"folder": folder.get().strip(), "frequency": frequency.get(), "time": time_value.get().strip(),
                      "day": next(key for key, name in DAY_NAMES.items() if name == day.get()),
                      "keep": int(keep.get()) if keep.get().strip().isdigit() else 0}
            dialog.destroy()
            self.run("Setting up scheduled backups…", lambda: self.service.set_backup_schedule(**values), self.show_schedule)
        ttk.Button(actions, text="Cancel", style="Secondary.TButton", command=dialog.destroy).pack(side="right")
        ttk.Button(actions, text="Save schedule", style="Primary.TButton", command=save).pack(side="right", padx=8)
        dialog.grab_set()

    def stop_schedule(self) -> None:
        if messagebox.askyesno("Turn off scheduled backups", "Stop backing up automatically? Existing backups are kept.",
                               parent=self.window):
            self.run("Turning off scheduled backups…", self.service.stop_backup_schedule, self.show_schedule)

    # Knowledge ---------------------------------------------------------------
    def knowledge_source(self) -> str:
        label = self.source.get()
        return next(key for key, value in KNOWLEDGE_SOURCES.items() if value == label)

    def load_knowledge(self) -> None:
        source = self.knowledge_source()
        self.run("Loading notes…", lambda: self.service.notes(source),
                 lambda rows: self.show_notes(rows, f"{len(rows):,} most recently changed notes" if len(rows) >= 500
                                              else f"{len(rows):,} notes"))

    def search_notes(self) -> None:
        query = self.knowledge_query.get().strip()
        if not query:
            self.reload("knowledge")
            return
        source = self.knowledge_source()
        self.loaded.add("knowledge")
        self.run("Searching notes…", lambda: self.service.search_knowledge(query, 20, source),
                 lambda rows: self.show_notes(rows, f"{len(rows)} best matches for “{query}”" if rows else f"No notes match “{query}”"))

    def show_notes(self, rows: list[dict], summary: str) -> None:
        self.note_rows = {row["path"]: row for row in rows}
        def folder(path: str) -> str:
            parent = PurePosixPath(path).parent.as_posix()
            return "—" if parent == "." else parent
        self.fill(self.note_table, ((row["path"], (clean(row.get("label") or row["title"], 120), folder(row["path"]),
                                                   short_time(row.get("modified"))), ())
                                    for row in rows))
        self.knowledge_count.configure(text=f"{summary} · {self.source.get()}")
        if rows:
            self.note_table.selection_set(rows[0]["path"])
        else:
            folder = self.service.knowledge_folder(self.knowledge_source())
            self.set_text(self.note_preview, f"No notes here yet.\n\n{folder}")

    def show_note(self) -> None:
        selected = self.note_table.selection()
        if not selected:
            return
        try:
            note = self.service.read_note(selected[0], self.knowledge_source())
        except Exception as exc:
            self.set_text(self.note_preview, f"Could not open this note: {exc}")
            return
        more = "\n\n[Preview shortened — open the file to read the rest.]" if note.get("truncated") else ""
        self.set_text(self.note_preview, f"{note['path']}\n\n{note['content']}{more}")

    # Memories ----------------------------------------------------------------
    def load_memories(self) -> None:
        query = self.memory_query.get().strip()
        kind = None if self.memory_kind.get() in ("", ALL_KINDS) else self.memory_kind.get()
        hide = self.hide_noise.get()
        self.run("Loading memories…", lambda: (self.service.memory_kinds(), self.service.memories(query, kind, hide_noise=hide)),
                 lambda result: self.show_memories(*result, query=query))

    def show_memories(self, kinds: list[str], rows: list[dict], *, query: str = "") -> None:
        self.memory_kind.configure(values=[ALL_KINDS, *kinds])
        self.memory_rows = {row["id"]: row for row in rows}
        self.fill(self.memory_table, ((row["id"], (short_time(row["created_at"]), row["kind"] or "—",
                                                   clean(row["content"], 160)), ())
                                      for row in rows))
        shown = f"{len(rows):,} newest" if len(rows) >= 300 else f"{len(rows):,}"
        hidden = " · noise hidden" if self.hide_noise.get() else ""
        self.memory_count.configure(text=f"{shown} memories{f' matching “{query}”' if query else ''}{hidden} · {self.service.world}")
        if rows:
            self.memory_table.selection_set(rows[0]["id"])
        else:
            self.set_text(self.memory_preview, "No memories match." if query else "No memories recorded for this project yet.")

    def show_memory(self) -> None:
        selected = self.memory_table.selection()
        row = self.memory_rows.get(selected[0]) if selected else None
        if row:
            self.set_text(self.memory_preview, f"{(row['kind'] or 'memory').title()} · {short_time(row['created_at'])}\n"
                                               f"Sector: {row['sector_id'] or '—'}\n\n{row['content']}")

    # Code map ----------------------------------------------------------------
    def load_code_map(self) -> None:
        self.run("Reading the code map…", self.service.code_map, self.show_code_map)

    def show_code_map(self, result: dict) -> None:
        cards = self.code_cards
        if not result.get("exists"):
            for value, note in cards.values():
                value.set("—")
                note.set("")
            cards["signals"][1].set("No code map yet. Run holocore atlas refresh in this project.")
            for tree in (self.kind_table, self.connected_table):
                tree.delete(*tree.get_children())
            return
        cards["signals"][0].set(f"{result['signals']:,}")
        cards["signals"][1].set(f"{result['code_signals']:,} of them are code; the rest are notes and concepts")
        cards["links"][0].set(f"{result['links']:,}")
        cards["files"][0].set(f"{result['files']:,}")
        cards["updated"][0].set(describe_capture(result["updated"]).split(" (")[-1].rstrip(")"))
        cards["updated"][1].set(short_time(result["updated"]))
        self.fill(self.kind_table, ((kind, (kind, f"{count:,}"), ()) for kind, count in result["kinds"]))
        self.fill(self.connected_table, ((str(i), (clean(row["label"], 80), row["kind"], f"{row['links']:,}", row["source"]), ())
                                         for i, row in enumerate(result["most_connected"])))


def _enable_sharp_text() -> None:
    """Render at the display's real resolution instead of letting Windows blur a scaled bitmap."""
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except (AttributeError, OSError):  # Not Windows, or awareness was already set.
        pass


def launch_desktop_app(*, project: str | Path | None = None, home: str | Path | None = None) -> int:
    """Open the control centre; with no arguments, use the selected Home and the most recent project."""
    from .home import HomeManager
    home = home or HomeManager().home
    _enable_sharp_text()
    window = tk.Tk()
    DesktopControlCentre(window, project or default_project(home), home=home)
    window.mainloop()
    return 0


def run_desktop_ui_smoke_test() -> int:
    _enable_sharp_text()
    window = tk.Tk()
    window.withdraw()
    app = DesktopControlCentre(window, Path.cwd(), autoload=False)
    try:
        window.update_idletasks()
        return 0 if window.title() == "HoloCore — Control Centre" else 1
    finally:
        app.close()
