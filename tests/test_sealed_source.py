import subprocess
import pytest

from ev4_architect_stage_qc import sealed_source
from ev4_architect_stage_qc.sealed_source import seal


def test_shallow_source_is_rejected(monkeypatch, tmp_path):
    monkeypatch.setattr(sealed_source, "_git", lambda *_: "true")
    with pytest.raises(RuntimeError, match="SEALED_SOURCE_SHALLOW_REPOSITORY"):
        seal(tmp_path, "a" * 40, tmp_path / "evidence")


def test_sealed_bundle_and_snapshot_bind_exact_commit(tmp_path, monkeypatch):
    root = subprocess.check_output(["git", "-C", ".", "rev-parse", "--show-toplevel"], text=True).strip()
    # This helper is exercised by the Architect integration checkout when available.
    import os
    source = os.environ.get("EV4_ARCHITECT_REPO")
    if not source:
        return
    commit = subprocess.check_output(["git", "-C", source, "rev-parse", "HEAD"], text=True).strip()
    value = seal(__import__("pathlib").Path(source), commit, tmp_path / "evidence")
    assert value.commit == commit and value.bundle.is_file() and value.snapshot.is_dir()
