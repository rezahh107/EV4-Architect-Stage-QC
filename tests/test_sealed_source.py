import subprocess

from ev4_architect_stage_qc.sealed_source import seal


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
