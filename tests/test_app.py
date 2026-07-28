from __future__ import annotations

import ast
from pathlib import Path
import re

import pytest

import ev4_architect_stage_qc.app as app_module
from ev4_architect_stage_qc.app import (
    STATUS_PRESENTATIONS,
    Application,
    status_presentation,
)
from ev4_architect_stage_qc.models import ConnectionResult, CoreResult
from ev4_architect_stage_qc.process_launcher import run_prefix_validation
from ev4_architect_stage_qc.theme import (
    status_danger,
    status_neutral,
    status_processing,
    status_success,
)


class Value:
    def __init__(self, value: str = ""):
        self.value = value

    def get(self) -> str:
        return self.value

    def set(self, value: str) -> None:
        self.value = value


class Button:
    def __init__(self):
        self.states: list[str] = []
        self.texts: list[str] = []
        self.visible = True
        self.state = "normal"

    def configure(self, **values):
        if "state" in values:
            self.state = values["state"]
            self.states.append(values["state"])
        if "text" in values:
            self.texts.append(values["text"])

    def grid(self):
        self.visible = True

    def grid_remove(self):
        self.visible = False


class Canvas:
    def __init__(self):
        self.fill = None

    def itemconfigure(self, item, **values):
        self.fill = values["fill"]


class Frame:
    def __init__(self):
        self.visible = False

    def grid(self):
        self.visible = True

    def grid_remove(self):
        self.visible = False


class Text:
    def __init__(self):
        self.value = ""
        self.focused = False

    def configure(self, **values):
        pass

    def delete(self, start, end):
        self.value = ""

    def insert(self, start, value):
        self.value = value

    def focus_set(self):
        self.focused = True


def status_application() -> Application:
    application = object.__new__(Application)
    application.status_label = Value()
    application.status_message = Value()
    application.status_details = Value()
    application.status_light = Canvas()
    application.status_light_id = 1
    application.details_button = Button()
    application.details_frame = Frame()
    application.details_text = Text()
    application.details_visible = False
    application.active = False
    application.last_attempt = Path("old-attempt")
    application.open_button = Button()
    return application


def operation_application() -> Application:
    application = status_application()
    application.architect = Value("selected/architect")
    application.folder = Value("selected/stages")
    application.terminal = Value("selected/final.json")
    application.verify_button = Button()
    application.prefix = Button()
    application.pref = Button()
    application.final = Button()
    application._input_widgets = [Button(), Button(), Button()]
    application._active_input_signature = None
    application.open_button.configure(state="normal")
    return application


def test_start_invalidates_prior_attempt_before_processing_and_worker_start(monkeypatch):
    application = operation_application()
    observed = {}
    original_show_processing = application._show_processing

    def observe_processing(kind):
        observed["before_processing"] = (
            application.last_attempt,
            application.open_button.state,
        )
        original_show_processing(kind)

    class FakeThread:
        def __init__(self, *, target, args, daemon):
            observed["at_construction"] = (
                application.last_attempt,
                application.open_button.state,
                application.status_label.get(),
            )
            self.target = target
            self.args = args
            self.daemon = daemon

        def start(self):
            observed["at_start"] = (
                application.last_attempt,
                application.open_button.state,
                application.status_label.get(),
            )

    application._show_processing = observe_processing
    monkeypatch.setattr(app_module.threading, "Thread", FakeThread)

    application._start("validation", lambda: None, ())

    assert observed["before_processing"] == (None, "disabled")
    assert observed["at_construction"] == (None, "disabled", "● Validating…")
    assert observed["at_start"] == (None, "disabled", "● Validating…")


@pytest.mark.parametrize("connection_ok", [True, False])
def test_connection_start_and_completion_never_publish_prior_result_folder(
    monkeypatch, connection_ok
):
    application = operation_application()

    class FakeThread:
        def __init__(self, **kwargs):
            pass

        def start(self):
            pass

    monkeypatch.setattr(app_module.threading, "Thread", FakeThread)
    monkeypatch.setattr(app_module, "save_architect_path", lambda path: None)

    application._start("connection", lambda: None, ())
    result = ConnectionResult(
        connection_ok,
        Path("selected/architect"),
        "a" * 40 if connection_ok else None,
        (
            "Compatible Architect Runtime authority-file identity closure."
            if connection_ok
            else "Authority verification failed."
        ),
        reference_commit="a" * 40 if connection_ok else None,
    )
    application._handle_connection(result)

    assert application.last_attempt is None
    assert application.open_button.state == "disabled"


def test_current_validation_attempt_is_the_only_result_folder_capability():
    application = status_application()
    current_attempt = Path("attempt-current")

    application._handle_validation(
        CoreResult(
            True,
            current_attempt,
            "PREFINAL_VALID",
            "Validation completed.",
            "Open the result folder.",
        )
    )

    assert application.last_attempt == current_attempt
    assert application.open_button.state == "normal"


def test_validation_without_attempt_clears_and_disables_result_folder_capability():
    application = status_application()
    application.open_button.configure(state="normal")

    application._handle_validation(
        CoreResult(
            False,
            None,
            "PREFINAL_INPUT_INVALID",
            "No attempt was created.",
            "Correct the selected input.",
        )
    )

    assert application.last_attempt is None
    assert application.open_button.state == "disabled"


def test_prefix_action_dispatches_only_through_launcher_with_selected_paths():
    application = object.__new__(Application)
    application.folder = Value("selected/stages")
    application.architect = Value("selected/architect")
    calls = []
    application._start = lambda kind, function, args: calls.append(
        (kind, function, args)
    )

    application.prefix_validation()

    assert calls == [
        (
            "validation",
            run_prefix_validation,
            (Path("selected/stages"), Path("selected/architect")),
        )
    ]


def test_prefix_button_participates_in_operation_state():
    application = object.__new__(Application)
    application.verify_button = Button()
    application.prefix = Button()
    application.pref = Button()
    application.final = Button()

    application._set_actions("disabled")
    application._set_actions("normal")

    for button in (
        application.verify_button,
        application.prefix,
        application.pref,
        application.final,
    ):
        assert button.states == ["disabled", "normal"]


def test_input_widgets_are_disabled_only_while_operation_is_running():
    application = object.__new__(Application)
    application._input_widgets = [Button(), Button(), Button()]

    application._set_inputs("disabled")
    application._set_inputs("normal")

    assert all(
        widget.states == ["disabled", "normal"]
        for widget in application._input_widgets
    )


def test_initial_state_is_neutral_not_success_or_failure():
    color, text = status_presentation("not_run", "Ready")

    assert color == status_neutral
    assert color not in {status_success, status_danger}
    assert text == "○ Ready"


def test_processing_state_replaces_previous_failure():
    application = status_application()
    application._set_status("failed", "Connection failed", "Retry required")

    application._show_processing("connection")

    assert application.status_light.fill == status_processing
    assert application.status_label.get() == "● Checking…"
    assert "fresh child process" in application.status_message.get()
    assert application.status_details.get() == ""


def test_verified_success_is_green_and_has_symbol_and_text():
    application = status_application()

    application._set_status(
        "passed",
        "Architect connection verified",
        "Compatible Runtime authority confirmed.",
    )

    assert application.status_light.fill == status_success
    assert application.status_label.get().startswith("✓ ")
    assert "verified" in application.status_label.get().lower()


def test_known_failure_is_red_and_has_actionable_text():
    application = status_application()

    application._set_status(
        "failed",
        "Connection failed",
        "The authority check failed.\nNext: select a compatible checkout.",
    )

    assert application.status_light.fill == status_danger
    assert application.status_label.get().startswith("✕ ")
    assert "Next:" in application.status_message.get()


def test_every_status_uses_symbol_and_text_not_color_alone():
    for state, presentation in STATUS_PRESENTATIONS.items():
        color, text = status_presentation(state)
        symbol, label = text.split(" ", 1)
        assert color
        assert symbol == presentation.symbol
        assert label == presentation.default_label
        assert label.strip()


def test_changing_relevant_input_invalidates_stale_success_and_result_folder():
    application = status_application()
    application._set_status("passed", "Validation passed", "Completed")

    application._invalidate_for_input(
        "Stage Output folder changed. Run validation again."
    )

    assert application.status_light.fill == status_neutral
    assert application.status_label.get() == "○ Not checked"
    assert application.last_attempt is None
    assert application.open_button.states[-1] == "disabled"


def test_technical_details_are_secondary_collapsed_and_keyboard_accessible():
    application = status_application()

    application._set_status(
        "failed",
        "Validation failed",
        "The selected files need correction.\nNext: correct them and retry.",
        "Code: PREFIX_INPUT_INVALID\nTechnical result: exact evidence",
    )

    assert "PREFIX_INPUT_INVALID" not in application.status_message.get()
    assert application.details_button.visible is True
    assert application.details_frame.visible is False
    assert application.details_text.value.startswith("Code: PREFIX_INPUT_INVALID")

    application._toggle_details()

    assert application.details_frame.visible is True
    assert application.details_button.texts[-1] == "Hide details"
    assert application.details_text.focused is True


def test_successful_connection_result_drives_verified_green(monkeypatch):
    application = status_application()
    monkeypatch.setattr(app_module, "save_architect_path", lambda path: None)
    result = ConnectionResult(
        True,
        Path("architect"),
        "a" * 40,
        "Compatible Architect Runtime authority-file identity closure.",
        reference_commit="a" * 40,
        runtime_interface_id="ev4-architect-quality-runtime@2.0.0",
        authority_file_count=32,
        child_pid=1234,
    )

    application._handle_connection(result)

    assert application.status_light.fill == status_success
    assert application.status_label.get() == "✓ Architect connection verified"
    assert application.status_message.get() == "Compatible Runtime authority confirmed."
    assert "Observed commit:" in application.status_details.get()


def test_failed_validation_result_drives_red_plain_message_and_exact_details():
    application = status_application()
    result = CoreResult(
        False,
        Path("attempt-0001"),
        "ARCHITECT_CONNECTION_INVALID",
        "Authority lock mismatch for committed blob: manifest.json",
        "Select a compatible local Architect repository.",
        child_pid=4321,
    )

    application._handle_validation(result)

    assert application.status_light.fill == status_danger
    assert application.status_label.get() == "✕ Validation failed"
    assert "required authority checks" in application.status_message.get()
    assert "Next:" in application.status_message.get()
    assert "Authority lock mismatch" in application.status_details.get()
    assert application.open_button.states[-1] == "normal"


def test_internal_error_uses_danger_state_without_traceback_as_primary_message():
    application = status_application()
    result = CoreResult(
        False,
        None,
        "INTERNAL_APPLICATION_ERROR",
        "Unexpected application error: RuntimeError: boom",
        "Review inputs and retry.",
    )

    application._handle_validation(result)

    assert application.status_light.fill == status_danger
    assert application.status_label.get() == "! Internal error"
    assert "RuntimeError" not in application.status_message.get()
    assert "RuntimeError" in application.status_details.get()


def _app_source() -> str:
    return (
        Path(__file__).resolve().parents[1]
        / "src/ev4_architect_stage_qc/app.py"
    ).read_text(encoding="utf-8")


def _section_grid_row(source: str, name: str) -> int:
    match = re.search(
        rf"self\.{name}\.grid\(\s*column=0,\s*row=(\d+),",
        source,
    )
    assert match is not None
    return int(match.group(1))


def test_three_main_sections_exist_in_required_vertical_order():
    source = _app_source()

    assert 'text="1. Architect Connection"' in source
    assert 'text="2. Validation"' in source
    assert 'text="3. Latest Result"' in source
    assert (
        _section_grid_row(source, "connection_frame")
        < _section_grid_row(source, "validation_frame")
        < _section_grid_row(source, "result_frame")
    )
    assert "self.main_frame.columnconfigure(0, weight=1)" in source
    assert "self.connection_frame.columnconfigure(0, weight=1)" in source
    assert "self.validation_frame.columnconfigure(0, weight=1)" in source
    assert "self.result_frame.columnconfigure(0, weight=1)" in source


def test_validation_controls_are_grouped_before_latest_result():
    source = _app_source()

    validation_start = source.index("self.validation_frame = ttk.LabelFrame")
    result_start = source.index("self.result_frame = ttk.LabelFrame")
    final_input = source.index('"Project Gate Export Request JSON"', validation_start)
    final_button = source.index("self.final = ttk.Button", validation_start)

    assert validation_start < final_input < final_button < result_start
    assert re.search(
        r"self\.prefix = ttk\.Button\(\s*self\.validation_frame,",
        source,
    )
    assert re.search(
        r"self\.pref = ttk\.Button\(\s*self\.validation_frame,",
        source,
    )
    assert re.search(
        r"self\.final = ttk\.Button\(\s*self\.validation_frame,",
        source,
    )


def test_result_actions_and_details_belong_to_latest_result():
    source = _app_source()

    assert "result_actions = ttk.Frame(self.result_frame)" in source
    assert re.search(
        r"self\.open_button = ttk\.Button\(\s*result_actions,",
        source,
    )
    assert re.search(
        r"self\.details_button = ttk\.Button\(\s*self\.result_frame,",
        source,
    )
    assert "self.details_frame = ttk.Frame(self.result_frame)" in source
    assert "self.details_frame.grid(column=0, row=4, sticky=\"nsew\")" in source
    assert "self.details_frame.grid_remove()" in source


def test_result_wrapping_is_bound_to_result_section_width():
    source = _app_source()

    assert (
        'self.result_frame.bind("<Configure>", self._resize_status_wrap, add="+")'
        in source
    )
    assert "if event.widget is self.result_frame:" in source
    assert "wraplength=max(320, event.width - 24)" in source


def test_conceptual_focus_order_follows_workflow_order():
    source = _app_source()
    labels = [
        '"Architect Repository"',
        '"Select Architect Repository"',
        '"Verify Architect Connection"',
        '"Stage Output Folder"',
        '"Select Stage Folder"',
        '"Validate Current Pipeline Prefix"',
        '"Run Prefinal Validation"',
        '"Project Gate Export Request JSON"',
        '"Select Export Request JSON"',
        '"Run Final Validation"',
        '"Open Result Folder"',
        '"Show details"',
    ]
    positions = [source.index(label) for label in labels]

    assert positions == sorted(positions)


def test_gui_declares_exact_prefix_label_and_imports_no_governed_runtime():
    source = _app_source()
    tree = ast.parse(source)
    imported = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    imported.update(
        node.module or ""
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    )

    assert '"Validate Current Pipeline Prefix"' in source
    assert "command=self.prefix_validation" in source
    assert not any("architect_adapter" in name for name in imported)
    assert not any(name.endswith(".core") for name in imported)
    assert not any("architect_quality_runtime" in name for name in imported)
