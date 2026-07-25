from __future__ import annotations

import os
import queue
import subprocess
import threading
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from .models import ConnectionResult, CoreResult
from .process_launcher import (
    run_final_validation,
    run_prefinal_validation,
    sibling_checkout,
    verify_connection,
)
from .settings import load_settings, save_architect_path
from .theme import apply


class Application:
    def __init__(self, root):
        self.root = root
        self.q = queue.Queue()
        self.active = False
        self.last_attempt = None
        self.architect = tk.StringVar()
        self.folder = tk.StringVar()
        self.terminal = tk.StringVar()
        self.status = tk.StringVar(value="○ Ready")
        self.detail = tk.StringVar(
            value="Select an Architect repository and Stage Output folder."
        )
        root.title("EV4 Architect Stage QC")
        root.minsize(720, 480)
        apply(root)
        self._build()
        root.protocol("WM_DELETE_WINDOW", self._close)
        root.after(100, self._poll)
        self._restore()

    def _build(self):
        frame = ttk.Frame(self.root, padding=16)
        frame.grid(sticky="nsew")
        self.root.columnconfigure(0, weight=1)
        frame.columnconfigure(1, weight=1)
        ttk.Label(
            frame,
            text="EV4 Architect Stage QC",
            style="Title.TLabel",
        ).grid(column=0, row=0, columnspan=3, sticky="w", pady=(0, 14))
        self._row(
            frame,
            1,
            "Architect Repository",
            self.architect,
            self.select_arch,
            "Select Architect Repository",
        )
        self.verify_button = ttk.Button(
            frame,
            text="Verify Architect Connection",
            command=self.connection,
        )
        self.verify_button.grid(column=2, row=2, sticky="e", pady=(0, 10))
        self._row(
            frame,
            3,
            "Stage Output Folder",
            self.folder,
            self.select_folder,
            "Select Stage Folder",
        )
        ttk.Label(
            frame,
            textvariable=self.status,
            style="Status.TLabel",
        ).grid(column=0, row=5, columnspan=3, sticky="w")
        ttk.Label(
            frame,
            textvariable=self.detail,
            wraplength=680,
        ).grid(column=0, row=6, columnspan=3, sticky="w", pady=(0, 10))
        self.pref = ttk.Button(
            frame,
            text="Run Prefinal Validation",
            command=self.prefinal,
        )
        self.pref.grid(column=0, row=7, sticky="w")
        self.open_button = ttk.Button(
            frame,
            text="Open Result Folder",
            command=self.open_result,
            state="disabled",
        )
        self.open_button.grid(column=1, row=7, sticky="w")
        self._row(
            frame,
            8,
            "Project Gate Export Request JSON",
            self.terminal,
            self.select_terminal,
            "Select Export Request JSON",
        )
        self.final = ttk.Button(
            frame,
            text="Run Final Validation",
            command=self.final_validation,
        )
        self.final.grid(column=0, row=10, sticky="w")
        for child in frame.winfo_children():
            child.grid_configure(padx=4)

    def _row(self, frame, row, label, variable, command, text):
        ttk.Label(frame, text=label).grid(column=0, row=row, sticky="w")
        ttk.Entry(frame, textvariable=variable).grid(
            column=1,
            row=row,
            sticky="ew",
        )
        ttk.Button(frame, text=text, command=command).grid(
            column=2,
            row=row,
            sticky="e",
            pady=(0, 10),
        )

    def _restore(self):
        saved = load_settings().get("architect_repository_path")
        automatic = sibling_checkout()
        path = Path(saved) if saved else automatic
        if path:
            self.architect.set(str(path))
            self.connection()

    def select_arch(self):
        path = filedialog.askdirectory(parent=self.root)
        if path:
            self.architect.set(path)
            self.connection()

    def select_folder(self):
        path = filedialog.askdirectory(parent=self.root)
        if path:
            self.folder.set(path)
            self.detail.set("Discovery complete; not yet evaluated.")

    def select_terminal(self):
        path = filedialog.askopenfilename(
            parent=self.root,
            filetypes=[("JSON files", "*.json")],
        )
        if path:
            self.terminal.set(path)

    def _set_actions(self, state: str):
        self.verify_button.configure(state=state)
        self.pref.configure(state=state)
        self.final.configure(state=state)

    def connection(self):
        if not self.architect.get():
            self.status.set("✕ Incompatible Architect runtime")
            self.detail.set("Select an Architect repository.")
            return
        self._start(
            "connection",
            verify_connection,
            (Path(self.architect.get()),),
        )

    def _start(self, kind, function, args):
        if self.active:
            return
        self.active = True
        self._set_actions("disabled")
        self.status.set("ℹ Running validation")
        self.detail.set("A fresh child interpreter is running one operation.")
        threading.Thread(
            target=self._worker,
            args=(kind, function, args),
            daemon=True,
        ).start()

    def _worker(self, kind, function, args):
        try:
            result = function(*args)
        except Exception as exc:
            if kind == "connection":
                result = ConnectionResult(
                    False,
                    Path(self.architect.get()),
                    None,
                    f"INTERNAL_APPLICATION_ERROR: {type(exc).__name__}: {exc}",
                )
            else:
                result = CoreResult(
                    False,
                    None,
                    "INTERNAL_APPLICATION_ERROR",
                    f"Unexpected application error: {type(exc).__name__}: {exc}",
                    "Review inputs and retry.",
                )
        self.q.put((kind, result))

    def prefinal(self):
        self._start(
            "validation",
            run_prefinal_validation,
            (Path(self.folder.get()), Path(self.architect.get())),
        )

    def final_validation(self):
        self._start(
            "validation",
            run_final_validation,
            (
                Path(self.folder.get()),
                Path(self.terminal.get()),
                Path(self.architect.get()),
            ),
        )

    def _handle_connection(self, result: ConnectionResult):
        if result.ok:
            save_architect_path(result.path)
            self.status.set("✓ Compatible Architect runtime")
            detail = (
                f"Observed commit: {result.commit}\n"
                f"Runtime interface: {result.runtime_interface_id}\n"
                f"Authority files: {result.authority_file_count} / "
                f"{result.authority_file_count} verified\n"
                f"Fresh child PID: {result.child_pid}"
            )
            if result.commit != result.reference_commit:
                detail += (
                    "\nRepository commit differs from the reference commit; "
                    "locked Authority files remain compatible."
                )
            self.detail.set(detail)
        else:
            self.status.set("✕ Incompatible Architect runtime")
            self.detail.set(result.reason)

    def _handle_validation(self, result: CoreResult):
        self.last_attempt = result.attempt_path
        self.open_button.configure(
            state="normal" if result.attempt_path else "disabled"
        )
        self.status.set(
            "✓ Validation completed successfully"
            if result.success
            else "✕ Validation failed"
        )
        self.detail.set(
            f"{result.code}: {result.reason}\n"
            f"Next action: {result.next_action}\n"
            f"Fresh child PID: {result.child_pid}"
        )

    def _poll(self):
        try:
            kind, result = self.q.get_nowait()
            self.active = False
            self._set_actions("normal")
            if kind == "connection":
                self._handle_connection(result)
            else:
                self._handle_validation(result)
        except queue.Empty:
            pass
        self.root.after(100, self._poll)

    def open_result(self):
        if self.last_attempt:
            if os.name == "nt":
                os.startfile(self.last_attempt)
            else:
                subprocess.Popen(["xdg-open", str(self.last_attempt)])

    def _close(self):
        if self.active:
            messagebox.showinfo(
                "Validation active",
                "The current validation must finish before closing.",
            )
            return
        self.root.destroy()
