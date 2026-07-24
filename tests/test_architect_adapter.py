import os
import json
import re
import subprocess
from pathlib import Path

import pytest

from ev4_architect_stage_qc.architect_adapter import LOCK_PATH, verify


LOCKED_FILE = "contracts/project-gate/producer-gate-export.v1.schema.json"


def authority_root():
    value = os.environ.get("EV4_ARCHITECT_REPO")
    if not value:
        pytest.skip("integration-only: set EV4_ARCHITECT_REPO to the locked checkout")
    return Path(value)


def _worktree(tmp_path, name):
    root = authority_root()
    worktree = tmp_path / name
    branch = f"qc-compatibility-{name}"
    subprocess.run(["git", "-C", str(root), "worktree", "add", "-b", branch, str(worktree), "HEAD"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(worktree), "config", "user.email", "qc@example.invalid"], check=True)
    subprocess.run(["git", "-C", str(worktree), "config", "user.name", "QC compatibility test"], check=True)
    return root, worktree


def _remove_worktree(root, worktree):
    subprocess.run(["git", "-C", str(root), "worktree", "remove", "--force", str(worktree)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(root), "branch", "-D", f"qc-compatibility-{worktree.name}"], check=True, capture_output=True)


def test_reference_checkout_loads_official_functions():
    connection = verify(authority_root())
    assert connection.ok, connection.reason
    assert connection.runtime is not None
    assert callable(connection.runtime.evaluate_run)
    assert callable(connection.runtime.evaluate_stage)
    assert connection.compatibility_mode == "authority_file_identity"
    assert connection.reference_commit == "338228cec0aeae951581690c3faba68f512e615c"


def test_wrong_checkout_blocks(tmp_path):
    assert not verify(tmp_path).ok


def test_wrong_repository_origin_is_rejected(tmp_path):
    root, worktree = _worktree(tmp_path, "wrong-origin")
    original = subprocess.check_output(["git", "-C", str(worktree), "config", "--get", "remote.origin.url"], text=True).strip()
    try:
        subprocess.run(["git", "-C", str(worktree), "remote", "set-url", "origin", "https://github.com/example/not-architect.git"], check=True)
        connection = verify(worktree)
        assert not connection.ok
        assert "identity" in connection.reason
    finally:
        subprocess.run(["git", "-C", str(worktree), "remote", "set-url", "origin", original], check=True)
        _remove_worktree(root, worktree)


def test_bundled_lock_is_cwd_independent(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert LOCK_PATH.is_file()


def test_provenance_uses_observed_checkout_commit():
    connection = verify(authority_root())
    assert connection.ok, connection.reason
    assert connection.trusted_context == {
        "producer_provenance": {
            "repository": "rezahh107/EV4-Architect-Repo",
            "ref": connection.ref,
            "commit_sha": connection.commit,
        }
    }


def test_descendant_unrelated_commit_is_compatible(tmp_path):
    root, worktree = _worktree(tmp_path, "descendant")
    try:
        doc = worktree / "docs/qc-compatibility-test.txt"
        doc.parent.mkdir(exist_ok=True)
        doc.write_text("unrelated documentation\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(worktree), "add", str(doc.relative_to(worktree))], check=True)
        subprocess.run(["git", "-C", str(worktree), "commit", "-m", "test: unrelated compatibility change"], check=True, capture_output=True)
        connection = verify(worktree)
        assert connection.ok, connection.reason
        assert connection.commit != connection.reference_commit
        assert len(connection.identities) == len(json.loads(LOCK_PATH.read_text())['files'])
    finally:
        _remove_worktree(root, worktree)


def test_dirty_unrelated_file_is_compatible(tmp_path):
    root, worktree = _worktree(tmp_path, "dirty-unrelated")
    try:
        (worktree / "unrelated.txt").write_text("not authority\n", encoding="utf-8")
        assert verify(worktree).ok
    finally:
        _remove_worktree(root, worktree)


def test_clean_crlf_authority_checkout_is_compatible(tmp_path):
    root, worktree = _worktree(tmp_path, "clean-crlf")
    original = subprocess.run(["git", "-C", str(worktree), "config", "--get", "core.autocrlf"], text=True, capture_output=True)
    try:
        subprocess.run(["git", "-C", str(worktree), "config", "core.autocrlf", "true"], check=True)
        target = worktree / LOCKED_FILE
        target.unlink()
        subprocess.run(["git", "-C", str(worktree), "checkout", "--", LOCKED_FILE], check=True)
        assert b"\r\n" in target.read_bytes()
        connection = verify(worktree)
        assert connection.ok, connection.reason
    finally:
        if original.returncode == 0:
            subprocess.run(["git", "-C", str(worktree), "config", "core.autocrlf", original.stdout.strip()], check=True)
        else:
            subprocess.run(["git", "-C", str(worktree), "config", "--unset-all", "core.autocrlf"], check=False)
        _remove_worktree(root, worktree)


def test_committed_authority_change_is_rejected(tmp_path):
    root, worktree = _worktree(tmp_path, "committed-authority")
    try:
        target = worktree / LOCKED_FILE
        target.write_bytes(target.read_bytes() + b"\n ")
        subprocess.run(["git", "-C", str(worktree), "add", LOCKED_FILE], check=True)
        subprocess.run(["git", "-C", str(worktree), "commit", "-m", "test: alter authority"], check=True, capture_output=True)
        connection = verify(worktree)
        assert not connection.ok
        assert connection.reason == f"Authority lock mismatch for committed blob: {LOCKED_FILE}"
    finally:
        _remove_worktree(root, worktree)


@pytest.mark.parametrize("attribute", ["filter=unsafe", "working-tree-encoding=UTF-16", "ident"])
def test_unsafe_authority_git_attribute_is_rejected(tmp_path, attribute):
    root, worktree = _worktree(tmp_path, "unsafe-attribute")
    try:
        (worktree / ".gitattributes").write_text(f"{LOCKED_FILE} {attribute}\n", encoding="utf-8")
        connection = verify(worktree)
        assert not connection.ok
        assert connection.reason.startswith(f"Unsafe authority Git attribute: {LOCKED_FILE}:")
    finally:
        _remove_worktree(root, worktree)


def test_dirty_authority_change_is_rejected(tmp_path):
    root, worktree = _worktree(tmp_path, "dirty-authority")
    try:
        target = worktree / LOCKED_FILE
        target.write_bytes(target.read_bytes() + b"\n ")
        connection = verify(worktree)
        assert not connection.ok
        assert connection.reason == f"Changed authority file: {LOCKED_FILE}"
    finally:
        _remove_worktree(root, worktree)


def test_missing_authority_file_is_rejected(tmp_path):
    root, worktree = _worktree(tmp_path, "missing-authority")
    try:
        (worktree / LOCKED_FILE).unlink()
        connection = verify(worktree)
        assert not connection.ok
        assert connection.reason == f"Required authority working-tree file missing: {LOCKED_FILE}"
    finally:
        _remove_worktree(root, worktree)


@pytest.mark.parametrize(("replacement", "expected"), [
    ("def evaluate_run_missing", "Official evaluator entry points are missing."),
    ("broken-load-authority", "Official authority compatibility failed: RuntimeError: broken authority"),
])
def test_runtime_contract_failures_are_rejected(tmp_path, replacement, expected):
    root, worktree = _worktree(tmp_path, "runtime-contract")
    try:
        lock = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
        lock["files"].pop("scripts/architect_quality_runtime.py")
        test_lock = tmp_path / "test.lock.json"
        test_lock.write_text(json.dumps(lock), encoding="utf-8")
        runtime = worktree / "scripts/architect_quality_runtime.py"
        source = runtime.read_text(encoding="utf-8")
        if replacement == "broken-load-authority":
            source = re.sub(r"def load_authority[^\n]*:", "def load_authority(root):\n    raise RuntimeError('broken authority')", source, count=1)
        else:
            source = source.replace("def evaluate_run", replacement, 1)
        runtime.write_text(source, encoding="utf-8")
        connection = verify(worktree, test_lock)
        assert not connection.ok
        assert connection.reason == expected
    finally:
        _remove_worktree(root, worktree)
