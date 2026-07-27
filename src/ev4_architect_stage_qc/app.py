from __future__ import annotations

import os
import queue
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from .models import ConnectionResult, CoreResult
from .process_launcher import (
    run_final_validation,
    run_prefix_validation,
    run_prefinal_validation,
    sibling_checkout,
    verify_connection,
)
from .settings import load_settings, save_architect_path
from .theme import STATUS_COLORS, apply


@dataclass(frozen=True)
class StatusPresentation:
    symbol: str
    default_label: str


STATUS_PRESENTATIONS = {
    "not_run": StatusPresentation("○", "Ready"),
    "processing": StatusPresentation("●", "Checking…"),
    "passed": StatusPresentation("✓", "Completed"),
    "failed": StatusPresentation("✕", "Needs correction"),
    "warning": StatusPresentation("⚠", "Needs review"),
    "internal_error": StatusPresentation("!", "Internal error"),
}


def status_presentation(state: str, label: str | None = None) -> tuple[str, str]:
    try:
        presentation = STATUS_PRESENTATIONS[state]
        color = STATUS_COLORS[state]
    except KeyError as exc:
        raise ValueError(f"Unknown status presentation state: {state}") from exc
    return color, f"{presentation.symbol} {label or presentation.default_label}"


class Application:
    def __init__(self, root):
        self.root = root
        self.q = queue.Queue()
        self.active = False
        self.last_attempt = None
        self._active_input_signature = None
        self._input_widgets = []
        self.architect = tk.StringVar()
        self.folder = tk.StringVar()
        self.terminal = tk.StringVar()
        self.status_label = tk.StringVar()
        self.status_message = tk.StringVar()
        self.status_details = tk.StringVar()
        self.details_visible = False
        root.title("EV4 Architect Stage QC")
        root.minsize(760, 560)
        apply(root)
        self._build()
        self._bind_input_invalidation()
        self._set_status(
            "not_run",
            "Ready",
            "Select an Architect repository and Stage Output folder.",
        )
        root.protocol("WM_DELETE_WINDOW", self._close)
        root.after(100, self._poll)
        self._restore()

    def _build(self):
        frame = ttk.Frame(self.root, padding=16)
        frame.grid(sticky="nsew")
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)
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

        status_frame = ttk.Frame(frame)
        status_frame.grid(column=0, row=5, columnspan=3, sticky="ew", pady=(4, 0))
        status_frame.columnconfigure(1, weight=1)
        self.status_light = tk.Canvas(
            status_frame,
            width=18,
            height=18,
            highlightthickness=0,
            borderwidth=0,
            background=self.root.cget("background"),
            takefocus=0,
        )
        self.status_light.grid(column=0, row=0, sticky="w", padx=(0, 8))
        self.status_light_id = self.status_light.create_oval(3, 3, 15, 15, outline="")
        ttk.Label(
            status_frame,
            textvariable=self.status_label,
            style="StatusTitle.TLabel",
        ).grid(column=1, row=0, sticky="w")

        self.status_message_label = ttk.Label(
            frame,
            textvariable=self.status_message,
            justify="left",
            wraplength=700,
        )
        self.status_message_label.grid(
            column=0,
            row=6,
            columnspan=3,
            sticky="ew",
            pady=(4, 2),
        )
        self.details_button = ttk.Button(
            frame,
            text="Show details",
            command=self._toggle_details,
        )
        self.details_button.grid(column=0, row=7, sticky="w", pady=(2, 6))
        self.details_button.grid_remove()

        self.details_frame = ttk.Frame(frame)
        self.details_frame.grid(column=0, row=8, columnspan=3, sticky="ew", pady=(0, 10))
        self.details_frame.columnconfigure(0, weight=1)
        self.details_text = tk.Text(
            self.details_frame,
            height=6,
            wrap="word",
            font=("Consolas", 9),
            relief="solid",
            borderwidth=1,
            padx=6,
            pady=6,
            takefocus=1,
        )
        self.details_text.grid(column=0, row=0, sticky="ew")
        details_scroll = ttk.Scrollbar(
            self.details_frame,
            orient="vertical",
            command=self.details_text.yview,
        )
        details_scroll.grid(column=1, row=0, sticky="ns")
        self.details_text.configure(yscrollcommand=details_scroll.set, state="disabled")
        self.details_frame.grid_remove()

        self.pref = ttk.Button(
            frame,
            text="Run Prefinal Validation",
            command=self.prefinal,
        )
        self.pref.grid(column=0, row=9, sticky="w")
        self.prefix = ttk.Button(
            frame,
            text="Validate Current Pipeline Prefix",
            command=self.prefix_validation,
        )
        self.prefix.grid(column=0, row=10, sticky="w", pady=(10, 0))
        self.open_button = ttk.Button(
            frame,
            text="Open Result Folder",
            command=self.open_result,
            state="disabled",
        )
        self.open_button.grid(column=1, row=9, sticky="w")
        self._row(
            frame,
            11,
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
        self.final.grid(column=0, row=13, sticky="w")
        for child in frame.winfo_children():
            child.grid_configure(padx=4)
        self.root.bind("<Configure>", self._resize_status_wrap, add="+")

    def _row(self, frame, row, label, variable, command, text):
        ttk.Label(frame, text=label).grid(column=0, row=row, sticky="w")
        entry = ttk.Entry(frame, textvariable=variable)
        entry.grid(column=1, row=row, sticky="ew")
        button = ttk.Button(frame, text=text, command=command)
        button.grid(column=2, row=row, sticky="e", pady=(0, 10))
        self._input_widgets.extend((entry, button))

    def _bind_input_invalidation(self):
        self.architect.trace_add(
            "write",
            lambda *_: self._invalidate_for_input(
                "Architect repository changed. Verify the connection again."
            ),
        )
        self.folder.trace_add(
            "write",
            lambda *_: self._invalidate_for_input(
                "Stage Output folder changed. Run validation again."
            ),
        )
        self.terminal.trace_add(
            "write",
            lambda *_: self._invalidate_for_input(
                "Export Request JSON changed. Run Final Validation again."
            ),
        )

    def _resize_status_wrap(self, event):
        if event.widget is self.root:
            self.status_message_label.configure(wraplength=max(420, event.width - 48))

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

    def select_terminal(self):
        path = filedialog.askopenfilename(
            parent=self.root,
            filetypes=[("JSON files", "*.json")],
        )
        if path:
            self.terminal.set(path)

    def _set_actions(self, state: str):
        self.verify_button.configure(state=state)
        self.prefix.configure(state=state)
        self.pref.configure(state=state)
        self.final.configure(state=state)

    def _set_inputs(self, state: str):
        for widget in self._input_widgets:
            widget.configure(state=state)

    def _input_signature(self) -> tuple[str, str, str]:
        return self.architect.get(), self.folder.get(), self.terminal.get()

    def _set_details_text(self, value: str):
        self.status_details.set(value)
        self.details_text.configure(state="normal")
        self.details_text.delete("1.0", "end")
        if value:
            self.details_text.insert("1.0", value)
        self.details_text.configure(state="disabled")

    def _hide_details(self):
        self.details_visible = False
        self.details_frame.grid_remove()
        self.details_button.configure(text="Show details")

    def _set_status(
        self,
        state: str,
        label: str,
        message: str,
        details: str = "",
    ):
        color, text = status_presentation(state, label)
        self.status_light.itemconfigure(self.status_light_id, fill=color)
        self.status_label.set(text)
        self.status_message.set(message)
        self._set_details_text(details)
        self._hide_details()
        if details:
            self.details_button.grid()
        else:
            self.details_button.grid_remove()

    def _toggle_details(self):
        if not self.status_details.get():
            return
        self.details_visible = not self.details_visible
        if self.details_visible:
            self.details_frame.grid()
            self.details_button.configure(text="Hide details")
            self.details_text.focus_set()
        else:
            self.details_frame.grid_remove()
            self.details_button.configure(text="Show details")

    def _invalidate_for_input(self, message: str):
        if self.active:
            return
        self.last_attempt = None
        if hasattr(self, "open_button"):
            self.open_button.configure(state="disabled")
        self._set_status("not_run", "Not checked", message)

    def connection(self):
        if not self.architect.get():
            self._set_status(
                "failed",
                "Connection failed",
                "No Architect repository is selected.\n"
                "Next: select an Architect repository and verify again.",
            )
            return
        self._start(
            "connection",
            verify_connection,
            (Path(self.architect.get()),),
        )

    def _show_processing(self, kind: str):
        if kind == "connection":
            label = "Checking…"
            message = (
                "Verifying the selected Architect repository in a fresh child process."
            )
        else:
            label = "Validating…"
            message = "Validating the selected inputs in a fresh child process."
        self._set_status("processing", label, message)

    def _start(self, kind, function, args):
        if self.active:
            return
        self.last_attempt = None
        self.open_button.configure(state="disabled")
        self.active = True
        self._active_input_signature = self._input_signature()
        self._set_actions("disabled")
        self._set_inputs("disabled")
        self._show_processing(kind)
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

    def prefix_validation(self):
        self._start(
            "validation",
            run_prefix_validation,
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

    @staticmethod
    def _connection_details(result: ConnectionResult) -> str:
        values = []
        if result.reason:
            values.append(f"Technical result: {result.reason}")
        if result.commit:
            values.append(f"Observed commit: {result.commit}")
        if result.reference_commit:
            values.append(f"Reference commit: {result.reference_commit}")
        if result.runtime_interface_id:
            values.append(f"Runtime interface: {result.runtime_interface_id}")
        if result.authority_file_count is not None:
            values.append(f"Authority files verified: {result.authority_file_count}")
        if result.child_pid is not None:
            values.append(f"Fresh child PID: {result.child_pid}")
        return "\n".join(values)

    def _handle_connection(self, result: ConnectionResult):
        details = self._connection_details(result)
        if result.ok:
            save_architect_path(result.path)
            if result.commit != result.reference_commit:
                details += (
                    "\nRepository commit differs from the reference commit; "
                    "locked Authority files remain compatible."
                )
            self._set_status(
                "passed",
                "Architect connection verified",
                "Compatible Runtime authority confirmed.",
                details,
            )
            return
        if result.reason.startswith("INTERNAL_APPLICATION_ERROR"):
            self._set_status(
                "internal_error",
                "Internal error",
                "The application could not complete the connection check.\n"
                "Next: review details, correct the input if needed, and retry.",
                details,
            )
            return
        self._set_status(
            "failed",
            "Connection failed",
            "The selected Architect repository did not pass the required authority checks.\n"
            "Next: select a compatible Architect checkout and verify again.",
            details,
        )

    @staticmethod
    def _validation_message(result: CoreResult) -> str:
        messages = {
            "ARCHITECT_CONNECTION_INVALID": (
                "The selected Architect repository did not pass the required authority checks."
            ),
            "PREFIX_INPUT_INVALID": "The selected Stage Output files could not be validated.",
            "PREFINAL_INPUT_INVALID": "The selected Stage Output files could not be validated.",
            "FINAL_INPUT_INVALID": "The selected final-validation inputs could not be validated.",
            "PREFIX_EVALUATION_UNCLASSIFIED": (
                "The Runtime could not classify the current Pipeline prefix safely."
            ),
            "PREFIX_NEEDS_INPUT": "The current Pipeline prefix needs additional input.",
            "PREFIX_BLOCKED": "The current Pipeline prefix needs correction.",
        }
        return messages.get(result.code, "The validation did not complete successfully.")

    @staticmethod
    def _validation_details(result: CoreResult) -> str:
        values = [f"Code: {result.code}", f"Technical result: {result.reason}"]
        if result.attempt_path:
            values.append(f"Attempt folder: {result.attempt_path}")
        if result.child_pid is not None:
            values.append(f"Fresh child PID: {result.child_pid}")
        return "\n".join(values)

    def _handle_validation(self, result: CoreResult):
        self.last_attempt = result.attempt_path
        self.open_button.configure(
            state="normal" if result.attempt_path else "disabled"
        )
        details = self._validation_details(result)
        if result.code == "INTERNAL_APPLICATION_ERROR":
            self._set_status(
                "internal_error",
                "Internal error",
                "The application could not complete validation.\n"
                f"Next: {result.next_action}",
                details,
            )
        elif result.success:
            self._set_status(
                "passed",
                "Validation passed",
                f"{result.reason}\nNext: {result.next_action}",
                details,
            )
        else:
            label = "Needs input" if result.code == "PREFIX_NEEDS_INPUT" else "Validation failed"
            self._set_status(
                "failed",
                label,
                f"{self._validation_message(result)}\nNext: {result.next_action}",
                details,
            )

    def _poll(self):
        try:
            kind, result = self.q.get_nowait()
            self.active = False
            self._set_actions("normal")
            self._set_inputs("normal")
            if self._input_signature() != self._active_input_signature:
                self._active_input_signature = None
                self._invalidate_for_input(
                    "Inputs changed while the operation was running. Run the operation again."
                )
            else:
                self._active_input_signature = None
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
