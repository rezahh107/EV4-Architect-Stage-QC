import json
import os
import re
import subprocess
from pathlib import Path

import pytest

import ev4_architect_stage_qc.architect_adapter as adapter
from ev4_architect_stage_qc.architect_adapter import LOCK_PATH, verify

LOCKED_FILE = "scripts/architect_quality_runtime.py"
EXPECTED_ARCHITECT_HEAD = "5eecc46ab0bf8a48a94714558706dd3f3e7b2faf"
EXPECTED_INTERFACE = "ev4-architect-quality-runtime@2.0.0"
EXPECTED_AUTHORITY_FILES = 18
BYTE_MISMATCH_REASON = f"Authority working-tree bytes differ from committed blob: {LOCKED_FILE}"


def authority_root():
    value = os.environ.get("EV4_ARCHITECT_REPO")
    if not value:
        pytest.skip("integration-only: set EV4_ARCHITECT_REPO to the locked checkout")
    return Path(value)


def _worktree(tmp_path, name):
    root = authority_root()
    worktree = tmp_path / name
    branch = f"qc-compatibility-{name}"
    subprocess.run(
        ["git", "-C", str(root), "worktree", "add", "-b", branch, str(worktree), "HEAD"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(worktree), "config", "user.email", "qc@example.invalid"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(worktree), "config", "user.name", "QC compatibility test"],
        check=True,
    )
    return root, worktree


def _remove_worktree(root, worktree):
    subprocess.run(
        ["git", "-C", str(root), "worktree", "remove", "--force", str(worktree)],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(root), "branch", "-D", f"qc-compatibility-{worktree.name}"],
        check=True,
        capture_output=True,
    )


def _set_index_flag(worktree: Path, flag: str, enabled: bool) -> None:
    option = f"--{flag}" if enabled else f"--no-{flag}"
    subprocess.run(
        ["git", "-C", str(worktree), "update-index", option, LOCKED_FILE],
        check=True,
        capture_output=True,
    )


def _assert_rejected_before_import(worktree: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    imported = False

    def fail_if_imported(*args, **kwargs):
        nonlocal imported
        imported = True
        raise AssertionError("Runtime import must not be attempted after authority byte mismatch")

    monkeypatch.setattr(adapter.importlib.util, "spec_from_file_location", fail_if_imported)
    connection = verify(worktree)
    assert not connection.ok
    assert connection.reason == BYTE_MISMATCH_REASON
    assert imported is False
    assert connection.runtime is None


def test_reference_checkout_loads_runtime_interface_v2():
    connection = verify(authority_root())
    assert connection.ok, connection.reason
    assert connection.runtime is not None
    assert callable(connection.runtime.evaluate_run)
    assert callable(connection.runtime.evaluate_stage)
    assert connection.runtime.RunContext is not None
    assert connection.runtime_interface_id == EXPECTED_INTERFACE
    assert connection.runtime.RUNTIME_INTERFACE_ID == EXPECTED_INTERFACE
    assert connection.compatibility_mode == "authority_file_identity"
    assert connection.reference_commit == EXPECTED_ARCHITECT_HEAD
    assert len(connection.identities) == EXPECTED_AUTHORITY_FILES


def test_connection_exposes_no_legacy_trusted_context():
    connection = verify(authority_root())
    assert connection.ok, connection.reason
    assert not hasattr(connection, "trusted_context")


def test_wrong_checkout_blocks(tmp_path):
    assert not verify(tmp_path).ok


def test_wrong_repository_origin_is_rejected(tmp_path):
    root, worktree = _worktree(tmp_path, "wrong-origin")
    original = subprocess.check_output(
        ["git", "-C", str(worktree), "config", "--get", "remote.origin.url"],
        text=True,
    ).strip()
    try:
        subprocess.run(
            [
                "git",
                "-C",
                str(worktree),
                "remote",
                "set-url",
                "origin",
                "https://github.com/example/not-architect.git",
            ],
            check=True,
        )
        connection = verify(worktree)
        assert not connection.ok
        assert "identity" in connection.reason
    finally:
        subprocess.run(
            ["git", "-C", str(worktree), "remote", "set-url", "origin", original],
            check=True,
        )
        _remove_worktree(root, worktree)


def test_bundled_lock_is_cwd_independent(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert LOCK_PATH.is_file()


def test_lock_records_exact_runtime_interface_and_head():
    lock = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    assert lock["reference_commit_sha"] == EXPECTED_ARCHITECT_HEAD
    assert lock["runtime_interface_id"] == EXPECTED_INTERFACE
    assert lock["identity_algorithm"] == "git_blob_oid_sha1"
    assert len(lock["files"]) == EXPECTED_AUTHORITY_FILES
    assert "scripts/architect_build_tree_validation.py" in lock["files"]
    assert "scripts/architect_payload_derivation_validation.py" in lock["files"]


def test_descendant_unrelated_commit_is_compatible(tmp_path):
    root, worktree = _worktree(tmp_path, "descendant")
    try:
        doc = worktree / "docs/qc-compatibility-test.txt"
        doc.parent.mkdir(exist_ok=True)
        doc.write_text("unrelated documentation\n", encoding="utf-8")
        subprocess.run(
            ["git", "-C", str(worktree), "add", str(doc.relative_to(worktree))],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(worktree), "commit", "-m", "test: unrelated compatibility change"],
            check=True,
            capture_output=True,
        )
        connection = verify(worktree)
        assert connection.ok, connection.reason
        assert connection.commit != connection.reference_commit
        assert len(connection.identities) == EXPECTED_AUTHORITY_FILES
    finally:
        _remove_worktree(root, worktree)


def test_dirty_unrelated_file_is_compatible(tmp_path):
    root, worktree = _worktree(tmp_path, "dirty-unrelated")
    try:
        (worktree / "unrelated.txt").write_text("not authority\n", encoding="utf-8")
        assert verify(worktree).ok
    finally:
        _remove_worktree(root, worktree)


def test_line_ending_only_authority_change_is_rejected(tmp_path):
    root, worktree = _worktree(tmp_path, "line-ending")
    try:
        target = worktree / LOCKED_FILE
        original = target.read_bytes()
        assert b"\n" in original
        target.write_bytes(original.replace(b"\n", b"\r\n"))
        connection = verify(worktree)
        assert not connection.ok
        assert connection.reason == BYTE_MISMATCH_REASON
    finally:
        _remove_worktree(root, worktree)


def test_committed_authority_change_is_rejected(tmp_path):
    root, worktree = _worktree(tmp_path, "committed-authority")
    try:
        target = worktree / LOCKED_FILE
        target.write_bytes(target.read_bytes() + b"\n# mutation\n")
        subprocess.run(["git", "-C", str(worktree), "add", LOCKED_FILE], check=True)
        subprocess.run(
            ["git", "-C", str(worktree), "commit", "-m", "test: alter authority"],
            check=True,
            capture_output=True,
        )
        connection = verify(worktree)
        assert not connection.ok
        assert connection.reason == f"Authority lock mismatch for committed blob: {LOCKED_FILE}"
    finally:
        _remove_worktree(root, worktree)


@pytest.mark.parametrize("attribute", ["filter=unsafe", "working-tree-encoding=UTF-16", "ident"])
def test_unsafe_authority_git_attribute_is_rejected(tmp_path, attribute):
    root, worktree = _worktree(tmp_path, f"unsafe-{attribute.split('=')[0]}")
    try:
        (worktree / ".gitattributes").write_text(
            f"{LOCKED_FILE} {attribute}\n", encoding="utf-8"
        )
        connection = verify(worktree)
        assert not connection.ok
        assert connection.reason.startswith(f"Unsafe authority Git attribute: {LOCKED_FILE}:")
    finally:
        _remove_worktree(root, worktree)


def test_dirty_authority_change_is_rejected_before_import(tmp_path, monkeypatch):
    root, worktree = _worktree(tmp_path, "dirty-authority")
    try:
        target = worktree / LOCKED_FILE
        target.write_bytes(target.read_bytes() + b"\n# mutation\n")
        _assert_rejected_before_import(worktree, monkeypatch)
    finally:
        _remove_worktree(root, worktree)


def test_assume_unchanged_authority_mutation_is_rejected_before_import(tmp_path, monkeypatch):
    root, worktree = _worktree(tmp_path, "assume-unchanged")
    flag_set = False
    try:
        _set_index_flag(worktree, "assume-unchanged", True)
        flag_set = True
        target = worktree / LOCKED_FILE
        target.write_bytes(target.read_bytes() + b"\n# hidden assume-unchanged mutation\n")
        diff = subprocess.run(
            ["git", "-C", str(worktree), "diff", "--quiet", "HEAD", "--", LOCKED_FILE],
            check=False,
        )
        assert diff.returncode == 0
        _assert_rejected_before_import(worktree, monkeypatch)
    finally:
        if flag_set:
            _set_index_flag(worktree, "assume-unchanged", False)
        _remove_worktree(root, worktree)


def test_skip_worktree_authority_mutation_is_rejected_before_import(tmp_path, monkeypatch):
    root, worktree = _worktree(tmp_path, "skip-worktree")
    flag_set = False
    try:
        try:
            _set_index_flag(worktree, "skip-worktree", True)
        except subprocess.CalledProcessError:
            pytest.skip("Git environment does not support skip-worktree for this fixture")
        flag_set = True
        target = worktree / LOCKED_FILE
        target.write_bytes(target.read_bytes() + b"\n# hidden skip-worktree mutation\n")
        _assert_rejected_before_import(worktree, monkeypatch)
    finally:
        if flag_set:
            _set_index_flag(worktree, "skip-worktree", False)
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


def test_authority_path_replaced_by_directory_is_rejected(tmp_path):
    root, worktree = _worktree(tmp_path, "authority-directory")
    try:
        target = worktree / LOCKED_FILE
        target.unlink()
        target.mkdir()
        connection = verify(worktree)
        assert not connection.ok
        assert connection.reason == f"Required authority working-tree file missing: {LOCKED_FILE}"
    finally:
        _remove_worktree(root, worktree)


def test_unavailable_committed_blob_is_rejected_before_import(tmp_path, monkeypatch):
    root, worktree = _worktree(tmp_path, "missing-blob")
    imported = False

    def no_blob(root_path, oid):
        return None

    def fail_if_imported(*args, **kwargs):
        nonlocal imported
        imported = True
        raise AssertionError("Runtime import must not be attempted")

    try:
        monkeypatch.setattr(adapter, "_committed_blob_bytes", no_blob)
        monkeypatch.setattr(adapter.importlib.util, "spec_from_file_location", fail_if_imported)
        connection = verify(worktree)
        assert not connection.ok
        assert connection.reason.startswith("Authority working-tree bytes differ from committed blob:")
        assert imported is False
    finally:
        _remove_worktree(root, worktree)


@pytest.mark.parametrize(
    ("replacement", "expected"),
    [
        ("missing-evaluate-run", "Official evaluator entry points are missing."),
        ("missing-run-context", "Official evaluator entry points are missing."),
        ("wrong-interface", "Official Runtime interface identity mismatch."),
        ("broken-load-authority", "Official authority compatibility failed: RuntimeError: broken authority"),
    ],
)
def test_runtime_contract_failures_are_rejected(tmp_path, replacement, expected):
    root, worktree = _worktree(tmp_path, f"runtime-{replacement}")
    try:
        lock = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
        lock["files"].pop(LOCKED_FILE)
        test_lock = tmp_path / f"{replacement}.lock.json"
        test_lock.write_text(json.dumps(lock), encoding="utf-8")
        runtime_path = worktree / LOCKED_FILE
        source = runtime_path.read_text(encoding="utf-8")
        if replacement == "broken-load-authority":
            original = (
                "def load_authority(\n"
                "    root: Path = ROOT,\n"
                ") -> tuple[dict[str, Any], dict[str, Any]]:\n"
            )
            replacement_source = (
                "def load_authority(root):\n"
                "    raise RuntimeError('broken authority')\n"
            )
            assert original in source
            source = source.replace(original, replacement_source, 1)
        elif replacement == "missing-evaluate-run":
            source = source.replace("def evaluate_run", "def evaluate_run_missing", 1)
        elif replacement == "missing-run-context":
            source = source.replace("class RunContext:", "class RemovedRunContext:", 1)
        else:
            source = source.replace(EXPECTED_INTERFACE, "ev4-architect-quality-runtime@9.9.9", 1)
        runtime_path.write_text(source, encoding="utf-8")
        connection = verify(worktree, test_lock)
        assert not connection.ok
        assert connection.reason == expected
    finally:
        _remove_worktree(root, worktree)
