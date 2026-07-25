from __future__ import annotations

import json
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
def canonical_test_lock_materialization(monkeypatch: pytest.MonkeyPatch):
    """Write synthetic *.lock.json fixtures as canonical LF bytes on every platform."""
    original = Path.write_text

    def write_text(
        path: Path,
        data: str,
        encoding: str | None = None,
        errors: str | None = None,
        newline: str | None = None,
    ) -> int:
        if path.name.endswith(".lock.json") and newline is None:
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
    """Derive the exact mutable interface-owner path from the committed Lock."""
    if request.node.name != "test_runtime_interface_mismatch_is_rejected_after_identity_checks":
        return
    selected = os.environ.get("EV4_ARCHITECT_REPO")
    if not selected:
        return
    repository_root = Path(__file__).resolve().parents[1]
    lock = json.loads(
        (repository_root / "architect-authority.lock.json").read_text(encoding="utf-8")
    )
    interface_id = lock["runtime_interface_id"]
    architect_root = Path(selected)
    owners = []
    for relative in sorted(lock["files"]):
        if not relative.endswith(".py"):
            continue
        try:
            source = (architect_root / relative).read_text(encoding="utf-8")
        except OSError:
            continue
        if interface_id in source:
            owners.append(relative)
    assert owners == ["scripts/architect_quality_runtime_core.py"]
    monkeypatch.setattr(request.module, "INTERFACE_FILE", owners[0])
