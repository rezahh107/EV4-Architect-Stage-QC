from __future__ import annotations

import os
from pathlib import Path

import pytest


@pytest.fixture(scope="session", autouse=True)
def deterministic_git_text_materialization():
    """Keep test-created Git worktrees byte-stable without changing user config."""
    keys = {
        "GIT_CONFIG_COUNT": "2",
        "GIT_CONFIG_KEY_0": "core.autocrlf",
        "GIT_CONFIG_VALUE_0": "false",
        "GIT_CONFIG_KEY_1": "core.eol",
        "GIT_CONFIG_VALUE_1": "lf",
    }
    previous = {key: os.environ.get(key) for key in keys}
    os.environ.update(keys)
    try:
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


@pytest.fixture(autouse=True)
def canonical_test_text_materialization(monkeypatch: pytest.MonkeyPatch):
    """Write synthetic Lock and Runtime-owner fixtures as canonical LF bytes."""
    original = Path.write_text

    def write_text(
        path: Path,
        data: str,
        encoding: str | None = None,
        errors: str | None = None,
        newline: str | None = None,
    ) -> int:
        if (
            path.name.endswith(".lock.json")
            or path.name == "architect_quality_runtime_core.py"
        ) and newline is None:
            newline = "\n"
        return original(
            path,
            data,
            encoding=encoding,
            errors=errors,
            newline=newline,
        )

    monkeypatch.setattr(Path, "write_text", write_text)


@pytest.fixture(autouse=True)
def selected_runtime_interface_owner(request, monkeypatch: pytest.MonkeyPatch):
    """Target the canonical public Runtime interface owner in its mutation test."""
    if request.node.name == "test_runtime_interface_mismatch_is_rejected_after_identity_checks":
        monkeypatch.setattr(
            request.module,
            "INTERFACE_FILE",
            "scripts/architect_quality_runtime_core.py",
        )
