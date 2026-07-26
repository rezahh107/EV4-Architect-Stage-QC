from __future__ import annotations

import ast
from pathlib import Path

from ev4_architect_stage_qc.app import Application
from ev4_architect_stage_qc.process_launcher import run_prefix_validation


class Value:
    def __init__(self, value: str):
        self.value = value

    def get(self) -> str:
        return self.value


class Button:
    def __init__(self):
        self.states: list[str] = []

    def configure(self, *, state: str):
        self.states.append(state)


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


def test_gui_declares_exact_prefix_label_and_imports_no_governed_runtime():
    source = (
        Path(__file__).resolve().parents[1]
        / "src/ev4_architect_stage_qc/app.py"
    ).read_text(encoding="utf-8")
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
