from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import ev4_architect_stage_qc.architect_adapter as adapter
from ev4_architect_stage_qc.architect_adapter import LOCK_PATH, MANIFEST_PATH, verify
from ev4_architect_stage_qc.lock_generator import generate_lock_bytes

LOCKED_FILE = "scripts/architect_quality_runtime.py"
INTERFACE_FILE = "scripts/architect_quality_runtime/__init__.py"
HISTORY_FILE = "scripts/architect_quality_runtime/history.py"
COMPATIBILITY_SIDECAR = "scripts/architect_project_gate_runtime_api.py"


def authority_root() -> Path:
    value = os.environ.get("EV4_ARCHITECT_REPO")
    if not value:
        pytest.skip("integration-only: set EV4_ARCHITECT_REPO to the selected checkout")
    return Path(value)


def _load_lock() -> dict:
    return json.loads(LOCK_PATH.read_text(encoding="utf-8"))


def _write_lock(tmp_path: Path, value: dict, name: str) -> Path:
    path = tmp_path / f"{name}.lock.json"
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _run(*args: str, cwd: Path, capture: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(cwd), *args],
        check=True,
        capture_output=capture,
        text=True,
    )


def _worktree(tmp_path: Path, name: str, start: str = "HEAD"):
    root = authority_root()
    worktree = tmp_path / name
    branch = f"qc-identity-{name}"
    _run("worktree", "add", "-b", branch, str(worktree), start, cwd=root)
    _run("config", "user.email", "qc@example.invalid", cwd=worktree)
    _run("config", "user.name", "QC compatibility test", cwd=worktree)
    return root, worktree, branch


def _detached_worktree(tmp_path: Path, name: str, commit: str):
    root = authority_root()
    worktree = tmp_path / name
    _run("worktree", "add", "--detach", str(worktree), commit, cwd=root)
    return root, worktree


def _remove_worktree(root: Path, worktree: Path, branch: str | None = None) -> None:
    _run("worktree", "remove", "--force", str(worktree), cwd=root)
    if branch:
        _run("branch", "-D", branch, cwd=root)


def _set_index_flag(worktree: Path, flag: str, enabled: bool) -> None:
    option = f"--{flag}" if enabled else f"--no-{flag}"
    _run("update-index", option, LOCKED_FILE, cwd=worktree)


def _assert_rejected_before_import(
    root: Path,
    monkeypatch: pytest.MonkeyPatch,
    expected_reason: str,
    lock_path: Path | None = None,
):
    imported = False

    def fail_if_imported(*args, **kwargs):
        nonlocal imported
        imported = True
        raise AssertionError("Runtime import must not be attempted")

    monkeypatch.setattr(adapter.importlib.util, "spec_from_file_location", fail_if_imported)
    connection = verify(root, lock_path)
    assert not connection.ok
    assert connection.reason == expected_reason
    assert connection.runtime is None
    assert imported is False
    return connection


def test_selected_checkout_loads_runtime_and_derives_expectations_from_lock():
    root = authority_root()
    lock = _load_lock()
    connection = verify(root)
    assert connection.ok, connection.reason
    assert connection.commit == lock["reference_commit_sha"]
    assert connection.reference_commit == lock["reference_commit_sha"]
    assert connection.runtime_interface_id == lock["runtime_interface_id"]
    assert connection.runtime.RUNTIME_INTERFACE_ID == lock["runtime_interface_id"]
    assert connection.compatibility_mode == lock["compatibility_mode"]
    assert set(connection.identities) == set(lock["files"])
    for symbol in (
        "RunContext",
        "ProjectGateFinalizationResult",
        "evaluate_run",
        "evaluate_stage",
        "finalize_project_gate",
    ):
        assert getattr(connection.runtime, symbol, None) is not None
    assert not hasattr(connection, "trusted_context")


def test_lock_inventory_equals_manifest_closure_plus_manifest():
    root = authority_root()
    lock = _load_lock()
    manifest = json.loads((root / MANIFEST_PATH).read_text(encoding="utf-8"))
    expected = {
        *manifest["python_authority_paths"],
        *manifest["data_authority_paths"],
        MANIFEST_PATH,
    }
    assert len(expected) == (
        len(manifest["python_authority_paths"])
        + len(manifest["data_authority_paths"])
        + 1
    )
    assert set(lock["files"]) == expected
    assert HISTORY_FILE in lock["files"]
    assert COMPATIBILITY_SIDECAR not in lock["files"]


def test_compatible_descendant_commit_changes_only_non_authority_content(tmp_path: Path):
    root, worktree, branch = _worktree(tmp_path, "descendant")
    try:
        doc = worktree / "docs/qc-compatible-descendant.txt"
        doc.parent.mkdir(exist_ok=True)
        doc.write_text("unrelated documentation\n", encoding="utf-8")
        _run("add", str(doc.relative_to(worktree)), cwd=worktree)
        _run("commit", "-m", "test: unrelated compatible descendant", cwd=worktree)
        connection = verify(worktree)
        assert connection.ok, connection.reason
        assert connection.commit != connection.reference_commit
        assert connection.reference_commit == _load_lock()["reference_commit_sha"]
    finally:
        _remove_worktree(root, worktree, branch)


def test_compatible_nonancestor_commit_with_identical_authority_tree(tmp_path: Path):
    root = authority_root()
    tree = _run("rev-parse", "HEAD^{tree}", cwd=root).stdout.strip()
    env = os.environ.copy()
    env.update(
        {
            "GIT_AUTHOR_NAME": "QC compatibility test",
            "GIT_AUTHOR_EMAIL": "qc@example.invalid",
            "GIT_COMMITTER_NAME": "QC compatibility test",
            "GIT_COMMITTER_EMAIL": "qc@example.invalid",
        }
    )
    orphan = subprocess.check_output(
        ["git", "-C", str(root), "commit-tree", tree, "-m", "test: identical orphan tree"],
        text=True,
        env=env,
    ).strip()
    root, worktree = _detached_worktree(tmp_path, "orphan", orphan)
    try:
        connection = verify(worktree)
        assert connection.ok, connection.reason
        assert connection.commit == orphan
        assert connection.commit != connection.reference_commit
    finally:
        _remove_worktree(root, worktree)


def test_unrelated_dirty_file_remains_compatible(tmp_path: Path):
    root, worktree, branch = _worktree(tmp_path, "dirty-unrelated")
    try:
        (worktree / "unrelated.txt").write_text("not authority\n", encoding="utf-8")
        connection = verify(worktree)
        assert connection.ok, connection.reason
    finally:
        _remove_worktree(root, worktree, branch)


def test_committed_authority_mutation_blocks_before_import(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    root, worktree, branch = _worktree(tmp_path, "committed-authority")
    try:
        target = worktree / LOCKED_FILE
        target.write_bytes(target.read_bytes() + b"\n# committed mutation\n")
        _run("add", LOCKED_FILE, cwd=worktree)
        _run("commit", "-m", "test: committed authority mutation", cwd=worktree)
        _assert_rejected_before_import(
            worktree,
            monkeypatch,
            f"Authority lock mismatch for committed blob: {LOCKED_FILE}",
        )
    finally:
        _remove_worktree(root, worktree, branch)


@pytest.mark.parametrize(
    "mode",
    ["ordinary", "line-ending", "assume-unchanged", "skip-worktree"],
)
def test_uncommitted_authority_mutations_block_before_import(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
):
    root, worktree, branch = _worktree(tmp_path, f"bytes-{mode}")
    flag: str | None = None
    try:
        if mode in {"assume-unchanged", "skip-worktree"}:
            flag = mode
            try:
                _set_index_flag(worktree, flag, True)
            except subprocess.CalledProcessError:
                pytest.skip(f"Git environment does not support {flag}")
        target = worktree / LOCKED_FILE
        original = target.read_bytes()
        if mode == "line-ending":
            assert b"\n" in original
            target.write_bytes(original.replace(b"\n", b"\r\n"))
        else:
            target.write_bytes(original + b"\n# working-tree mutation\n")
        _assert_rejected_before_import(
            worktree,
            monkeypatch,
            f"Authority working-tree bytes differ from committed blob: {LOCKED_FILE}",
        )
    finally:
        if flag:
            _set_index_flag(worktree, flag, False)
        _remove_worktree(root, worktree, branch)


def test_missing_locked_file_blocks_before_import(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    root, worktree, branch = _worktree(tmp_path, "missing-authority")
    try:
        (worktree / LOCKED_FILE).unlink()
        _assert_rejected_before_import(
            worktree,
            monkeypatch,
            f"Required authority working-tree file missing: {LOCKED_FILE}",
        )
    finally:
        _remove_worktree(root, worktree, branch)


def test_locked_file_replaced_by_directory_blocks_before_import(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    root, worktree, branch = _worktree(tmp_path, "authority-directory")
    try:
        target = worktree / LOCKED_FILE
        target.unlink()
        target.mkdir()
        _assert_rejected_before_import(
            worktree,
            monkeypatch,
            f"Required authority working-tree file missing: {LOCKED_FILE}",
        )
    finally:
        _remove_worktree(root, worktree, branch)


def test_committed_blob_unavailable_blocks_before_import(
    monkeypatch: pytest.MonkeyPatch,
):
    lock = _load_lock()
    target_oid = lock["files"][LOCKED_FILE]
    original = adapter._committed_blob_bytes

    def unavailable(root: Path, oid: str):
        if oid == target_oid:
            return None
        return original(root, oid)

    monkeypatch.setattr(adapter, "_committed_blob_bytes", unavailable)
    _assert_rejected_before_import(
        authority_root(),
        monkeypatch,
        f"Committed authority blob unavailable: {LOCKED_FILE}",
    )


def test_wrong_repository_origin_blocks_before_import(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    root, worktree, branch = _worktree(tmp_path, "wrong-origin")
    original = _run("config", "--get", "remote.origin.url", cwd=worktree).stdout.strip()
    try:
        _run("remote", "set-url", "origin", "https://github.com/example/not-architect.git", cwd=worktree)
        _assert_rejected_before_import(
            worktree,
            monkeypatch,
            "Selected checkout identity is not rezahh107/EV4-Architect-Repo.",
        )
    finally:
        _run("remote", "set-url", "origin", original, cwd=worktree)
        _remove_worktree(root, worktree, branch)


def test_malformed_reference_commit_blocks_before_import(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    lock = _load_lock()
    lock["reference_commit_sha"] = "not-a-commit"
    path = _write_lock(tmp_path, lock, "malformed-reference")
    _assert_rejected_before_import(
        authority_root(),
        monkeypatch,
        "QC authority compatibility lock reference commit is malformed.",
        path,
    )


@pytest.mark.parametrize(
    ("mutation", "expected"),
    [
        ("missing-manifest", f"Authority Lock is missing Manifest path: {MANIFEST_PATH}"),
        ("missing-history", f"Authority Lock is missing Manifest path: {HISTORY_FILE}"),
        (
            "extra-sidecar",
            f"Authority Lock has extra path not in Manifest: {COMPATIBILITY_SIDECAR}",
        ),
        (
            "wrong-manifest-oid",
            f"Authority lock mismatch for committed blob: {MANIFEST_PATH}",
        ),
        (
            "wrong-authority-oid",
            f"Authority lock mismatch for committed blob: {LOCKED_FILE}",
        ),
    ],
)
def test_lock_inventory_and_oid_mutations_block_before_import(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
    expected: str,
):
    lock = _load_lock()
    if mutation == "missing-manifest":
        lock["files"].pop(MANIFEST_PATH)
    elif mutation == "missing-history":
        lock["files"].pop(HISTORY_FILE)
    elif mutation == "extra-sidecar":
        lock["files"][COMPATIBILITY_SIDECAR] = "0" * 40
    elif mutation == "wrong-manifest-oid":
        lock["files"][MANIFEST_PATH] = "1" * 40
    elif mutation == "wrong-authority-oid":
        lock["files"][LOCKED_FILE] = "2" * 40
    path = _write_lock(tmp_path, lock, mutation)
    _assert_rejected_before_import(authority_root(), monkeypatch, expected, path)


def test_runtime_interface_mismatch_is_rejected_after_identity_checks(tmp_path: Path):
    root, worktree, branch = _worktree(tmp_path, "wrong-interface")
    try:
        target = worktree / INTERFACE_FILE
        source = target.read_text(encoding="utf-8")
        expected_interface = _load_lock()["runtime_interface_id"]
        assert expected_interface in source
        target.write_text(
            source.replace(expected_interface, "ev4-architect-quality-runtime@9.9.9", 1),
            encoding="utf-8",
        )
        _run("add", INTERFACE_FILE, cwd=worktree)
        _run("commit", "-m", "test: wrong Runtime interface", cwd=worktree)
        commit = _run("rev-parse", "HEAD", cwd=worktree).stdout.strip()
        lock_path = tmp_path / "wrong-interface.lock.json"
        lock_path.write_bytes(generate_lock_bytes(worktree, expected_commit=commit))
        for name in list(sys.modules):
            if name == "architect_quality_runtime" or name.startswith("architect_quality_runtime."):
                sys.modules.pop(name, None)
        connection = verify(worktree, lock_path)
        assert not connection.ok
        assert connection.reason == "Official Runtime interface identity mismatch."
    finally:
        _remove_worktree(root, worktree, branch)



def test_noncanonical_lock_blocks_before_import(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    lock = _load_lock()
    path = tmp_path / "noncanonical.lock.json"
    path.write_text(json.dumps(lock, separators=(",", ":")), encoding="utf-8")
    _assert_rejected_before_import(
        authority_root(),
        monkeypatch,
        "QC authority compatibility lock is noncanonical.",
        path,
    )

def test_bundled_lock_is_cwd_independent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.chdir(tmp_path)
    assert LOCK_PATH.is_file()
