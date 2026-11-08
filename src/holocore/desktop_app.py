"""Native HoloCore desktop control centre with background service operations."""
from __future__ import annotations

import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from .desktop_service import DesktopService


class DesktopControlCentre:
    def __init__(self, window: tk.Tk, root: str | Path, *, home: str | Path | None = None, autoload: bool = True):
        self.window = window
        self.home = home
        self.service = DesktopService(root, home)
        self.events = queue.Queue()
        self.busy = False
        self.closed = False
        self.project_rows = []
        window.title("HoloCore — Control Centre")
        window.geometry("1000x720")
        window.minsize(760, 520)
        window.protocol("WM_DELETE_WINDOW", self.close)
        container = ttk.Frame(window, padding=20)
        container.pack(fill="both", expand=True)
        ttk.Label(container, text="HoloCore", font=("Segoe UI", 22, "bold")).pack(anchor="w")
        ttk.Label(container, text="Your projects, knowledge, and AI connections").pack(anchor="w", pady=(0, 16))
        project_bar = ttk.Frame(container)
        project_bar.pack(fill="x")
        ttk.Label(project_bar, text="Project").pack(side="left", padx=(0, 8))
        self.project = ttk.Combobox(project_bar, state="readonly", width=48)
        self.project.pack(side="left", fill="x", expand=True)
        self.project.bind("<<ComboboxSelected>>", self.select_project)
        self.refresh_button = ttk.Button(project_bar, text="Refresh", command=self.refresh)
        self.refresh_button.pack(side="left", padx=8)
        self.folder_button = ttk.Button(project_bar, text="Choose project folder", command=self.choose_project)
        self.folder_button.pack(side="left")
        tabs = ttk.Notebook(container)
        tabs.pack(fill="both", expand=True, pady=16)
        overview = ttk.Frame(tabs, padding=12)
        search = ttk.Frame(tabs, padding=12)
        connections = ttk.Frame(tabs, padding=12)
        self.storage_tab = ttk.Frame(tabs, padding=12)
        for frame, label in ((overview, "Overview"), (search, "Knowledge"), (connections, "AI Connections"), (self.storage_tab, "Storage & Backups")):
            tabs.add(frame, text=label)
        self.overview_text = self.text_area(overview)
        bar = ttk.Frame(search)
        bar.pack(fill="x")
        ttk.Label(bar, text="Search project knowledge").pack(side="left")
        self.query = ttk.Entry(bar)
        self.query.pack(side="left", fill="x", expand=True, padx=8)
        self.query.bind("<Return>", lambda _event: self.search())
        self.search_button = ttk.Button(bar, text="Search", command=self.search)
        self.search_button.pack(side="left")
        self.search_text = self.text_area(search)
        ttk.Label(connections, text="Configuration checks do not confirm a live AI session or successful capture.", wraplength=700).pack(anchor="w", pady=(0, 10))
        self.connection_text = self.text_area(connections)
        storage_bar = ttk.Frame(self.storage_tab)
        storage_bar.pack(fill="x")
        self.backup_button = ttk.Button(storage_bar, text="Back up memories", command=self.backup)
        self.backup_button.pack(side="left")
        self.preview_button = ttk.Button(storage_bar, text="Check restore backup", command=self.restore_preview)
        self.preview_button.pack(side="left", padx=8)
        ttk.Label(self.storage_tab, text="Backups include the memory and activity database. Knowledge notes and original chat files need separate backups.", wraplength=700).pack(anchor="w", pady=8)
        self.storage_text = self.text_area(self.storage_tab)
        self.status = tk.StringVar(value="Ready")
        ttk.Label(container, textvariable=self.status, wraplength=900).pack(anchor="w")
        self.poll_id = window.after(75, self.poll)
        if autoload:
            self.refresh()

    @staticmethod
    def text_area(parent):
        frame = ttk.Frame(parent)
        frame.pack(fill="both", expand=True, pady=8)
        scrollbar = ttk.Scrollbar(frame)
        scrollbar.pack(side="right", fill="y")
        text = tk.Text(frame, wrap="word", font=("Segoe UI", 11), padx=12, pady=12,
                       state="disabled", yscrollcommand=scrollbar.set)
        text.pack(fill="both", expand=True)
        scrollbar.configure(command=text.yview)
        return text

    @staticmethod
    def set_text(widget, value):
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", value)
        widget.configure(state="disabled")

    def run(self, label, operation, callback):
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

    def set_busy(self, busy):
        for button in (self.refresh_button, self.folder_button, self.search_button, self.backup_button, self.preview_button):
            button.configure(state="disabled" if busy else "normal")
        self.project.configure(state="disabled" if busy else "readonly")

    def poll(self):
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
        self.poll_id = self.window.after(75, self.poll)

    def refresh(self):
        self.run("Checking project and connections…", self.service.overview, self.show_overview)

    def show_overview(self, result):
        self.project_rows = result["projects"]
        if not any(Path(row["root"]).resolve() == self.service.root for row in self.project_rows):
            self.project_rows.insert(0, {"name": result["project"], "root": result["root"]})
        self.project.configure(values=[row["name"] + " — " + row["root"] for row in self.project_rows])
        self.project.current(next(i for i, row in enumerate(self.project_rows) if Path(row["root"]).resolve() == self.service.root))
        self.set_text(self.overview_text, f"{result['project']}\n\nProject folder: {result['root']}\nFolder available: {'Yes' if result['available'] else 'No'}\n\nKnowledge: {'Available' if result['knowledge_ready'] else 'Not set up'}\n{result['knowledge']}\n\nMemories: {'Available' if result['memory_ready'] else 'Not set up'}\n\nChoose another registered project above or select a folder. Use HoloCore Setup to register a new project and connect AI tools.")
        self.set_text(self.connection_text, "\n\n".join(f"{row['name'].title()} — {row['state']}\n{row['detail']}\nLast successful capture: not verified\nConfiguration: {row['config_path']}" for row in result["connections"]))
        storage = result["storage"]
        details = (f"Database: {storage.get('database_bytes', 0):,} bytes\nPending journal: {storage.get('wal_bytes', 0):,} bytes\nTotal storage: {storage.get('total_bytes', 0):,} bytes\nReusable space: {storage.get('reusable_bytes', 0):,} bytes\n\nIntegrity is checked when creating or checking a backup." if storage["exists"] else "No memory database exists for this project yet.")
        self.set_text(self.storage_text, f"Memory database\n{result['database']}\n\n{details}")

    def select_project(self, _event=None):
        if not self.busy and self.project.current() >= 0:
            self.switch(self.project_rows[self.project.current()]["root"])

    def choose_project(self):
        folder = filedialog.askdirectory(parent=self.window, title="Choose project folder")
        if folder:
            self.switch(folder)

    def switch(self, root):
        try:
            self.service = DesktopService(root, self.home)
        except Exception as exc:
            messagebox.showerror("Could not open project", str(exc), parent=self.window)
            return
        self.set_text(self.search_text, "")
        self.refresh()

    def search(self):
        query = self.query.get().strip()
        if query:
            self.run("Searching project knowledge…", lambda: self.service.search_knowledge(query),
                     lambda rows: self.set_text(self.search_text, "\n\n".join(f"{row['title']}\n{row['snippet']}\nSource: {row['path']}" for row in rows) or "No matching knowledge found."))

    def close(self):
        self.closed = True
        self.window.after_cancel(self.poll_id)
        self.window.destroy()

    def backup(self):
        directory = filedialog.askdirectory(parent=self.window, title="Choose backup folder")
        if directory:
            self.run("Creating and checking memory backup…", lambda: self.service.backup(directory),
                     lambda result: self.set_text(self.storage_text, f"Backup created and integrity checked\n\n{result['backup']}\n\nSize: {result['bytes']:,} bytes\n\nKnowledge notes and original chat files are not included."))

    def restore_preview(self):
        backup = filedialog.askopenfilename(parent=self.window, title="Choose memory backup to check", filetypes=[("SQLite backups", "*.sqlite3 *.db"), ("All files", "*.*")])
        if backup:
            self.run("Checking restore candidate…", lambda: self.service.restore_preview(backup),
                     lambda result: self.set_text(self.storage_text, f"Backup is valid\n\n{result['backup']}\n\nTarget memory database:\n{result['database']}\n\nNo data has been restored. Close all HoloCore writers before a recovery operation. This interface currently validates backups only."))


def launch_desktop_app(*, project: str | Path | None = None, home: str | Path | None = None) -> int:
    window = tk.Tk()
    DesktopControlCentre(window, project or Path.cwd(), home=home)
    window.mainloop()
    return 0


def run_desktop_ui_smoke_test() -> int:
    window = tk.Tk()
    window.withdraw()
    app = DesktopControlCentre(window, Path.cwd(), autoload=False)
    try:
        window.update_idletasks()
        return 0 if window.title() == "HoloCore — Control Centre" else 1
    finally:
        app.close()
