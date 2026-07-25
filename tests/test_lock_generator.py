from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

import pytest

from ev4_architect_stage_qc.architect_adapter import LOCK_PATH, MANIFEST_PATH
from ev4_architect_stage_qc.lock_generator import (
    EXPECTED_INTERFACE,
    EXPECTED_REPOSITORY,
    LockGenerationError,
    check_lock,
    generate_lock_bytes,
    generate_lock_document,
    read_reference_commit,
    write_lock,
)


def _run(*args: str, cwd: Path) -> str:
    return subprocess.check_output(
        ["git", "-C", str(cwd), *args],
        text=True,
        stderr=subprocess.DEVNULL,
    ).strip()


def _commit(root: Path, message: str) -> str:
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
    subprocess.run(
        ["git", "-C", str(root), "commit", "-m", message],
        check=True,
        capture_output=True,
    )
    return _run("rev-parse", "HEAD", cwd=root)


def _fixture_repo(tmp_path: Path) -> tuple[Path, str]:
    root = tmp_path / "Architect"
    root.mkdir()
    subprocess.run(["git", "init", str(root)], check=True, capture_output=True)
    _run("config", "user.email", "qc@example.invalid", cwd=root)
    _run("config", "user.name", "QC lock generator test", cwd=root)
    _run(
        "remote",
        "add",
        "origin",
        "https://github.com/rezahh107/EV4-Architect-Repo.git",
        cwd=root,
    )

    (root / "scripts").mkdir()
    (root / "contracts").mkdir()
    (root / "manifests").mkdir()
    (root / "scripts/architect_quality_runtime.py").write_text(
        "RUNTIME_INTERFACE_ID = 'ev4-architect-quality-runtime@2.0.0'\n",
        encoding="utf-8",
    )
    (root / "scripts/one.py").write_text("VALUE = 1\n", encoding="utf-8")
    (root / "contracts/one.json").write_text("{}\n", encoding="utf-8")
    manifest = {
        "manifest_id": "fixture",
        "manifest_version": "1.0.0",
        "owner_repository": EXPECTED_REPOSITORY,
        "runtime_interface_id": EXPECTED_INTERFACE,
        "public_entry_points": [
            {
                "path": "scripts/architect_quality_runtime.py",
                "symbols": ["finalize_project_gate"],
            }
        ],
        "python_authority_paths": [
            "scripts/architect_quality_runtime.py",
            "scripts/one.py",
        ],
        "data_authority_paths": ["contracts/one.json"],
    }
    (root / MANIFEST_PATH).write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return root, _commit(root, "fixture authority")


def _integration_root() -> Path:
    value = os.environ.get("EV4_ARCHITECT_REPO")
    if not value:
        pytest.skip("integration-only: set EV4_ARCHITECT_REPO to the selected checkout")
    return Path(value)


def test_selected_architect_generates_exact_committed_lock():
    root = _integration_root()
    reference = read_reference_commit(LOCK_PATH)
    assert generate_lock_bytes(root, expected_commit=reference) == LOCK_PATH.read_bytes()
    document = generate_lock_document(root, expected_commit=reference)
    assert document["reference_commit_sha"] == reference
    assert document["runtime_interface_id"] == EXPECTED_INTERFACE
    assert MANIFEST_PATH in document["files"]


def test_generator_is_byte_deterministic(tmp_path: Path):
    root, commit = _fixture_repo(tmp_path)
    first = generate_lock_bytes(root, expected_commit=commit)
    second = generate_lock_bytes(root, expected_commit=commit)
    assert first == second


def test_check_mode_is_side_effect_free(tmp_path: Path):
    root, commit = _fixture_repo(tmp_path)
    lock = tmp_path / "lock.json"
    write_lock(root, lock, expected_commit=commit)
    before = lock.read_bytes()
    before_stat = lock.stat()
    assert check_lock(root, lock, expected_commit=commit)
    after_stat = lock.stat()
    assert lock.read_bytes() == before
    assert after_stat.st_mtime_ns == before_stat.st_mtime_ns
    assert after_stat.st_size == before_stat.st_size


@pytest.mark.parametrize(
    "mutation",
    [
        "omit-python",
        "omit-data",
        "omit-manifest",
        "wrong-oid",
        "noncanonical-bytes",
    ],
)
def test_lock_drift_is_detected(tmp_path: Path, mutation: str):
    root, commit = _fixture_repo(tmp_path)
    lock = tmp_path / "lock.json"
    write_lock(root, lock, expected_commit=commit)
    value = json.loads(lock.read_text(encoding="utf-8"))
    manifest = json.loads((root / MANIFEST_PATH).read_text(encoding="utf-8"))
    if mutation == "omit-python":
        value["files"].pop(manifest["python_authority_paths"][0])
        lock.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    elif mutation == "omit-data":
        value["files"].pop(manifest["data_authority_paths"][0])
        lock.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    elif mutation == "omit-manifest":
        value["files"].pop(MANIFEST_PATH)
        lock.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    elif mutation == "wrong-oid":
        first = sorted(value["files"])[0]
        value["files"][first] = "0" * 40
        lock.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    else:
        lock.write_text(json.dumps(value, separators=(",", ":")), encoding="utf-8")
    assert not check_lock(root, lock, expected_commit=commit)


def test_manifest_authority_drift_makes_existing_lock_stale(tmp_path: Path):
    root, commit = _fixture_repo(tmp_path)
    lock = tmp_path / "lock.json"
    write_lock(root, lock, expected_commit=commit)
    manifest_path = root / MANIFEST_PATH
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["python_authority_paths"].append("scripts/two.py")
    (root / "scripts/two.py").write_text("VALUE = 2\n", encoding="utf-8")
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    new_commit = _commit(root, "add authority path")
    assert not check_lock(root, lock, expected_commit=new_commit)


def test_malformed_expected_commit_is_rejected(tmp_path: Path):
    root, _ = _fixture_repo(tmp_path)
    with pytest.raises(LockGenerationError, match="40 lowercase hexadecimal"):
        generate_lock_bytes(root, expected_commit="not-a-commit")


def test_malformed_lock_reference_is_rejected(tmp_path: Path):
    lock = tmp_path / "lock.json"
    lock.write_text('{"reference_commit_sha":"INVALID"}\n', encoding="utf-8")
    with pytest.raises(LockGenerationError, match="40 lowercase hexadecimal"):
        read_reference_commit(lock)


@pytest.mark.parametrize(
    "bad_path",
    ["../outside.py", "/absolute.py", "scripts\\windows.py", "scripts//double.py"],
)
def test_noncanonical_manifest_paths_are_rejected(tmp_path: Path, bad_path: str):
    root, _ = _fixture_repo(tmp_path)
    manifest_path = root / MANIFEST_PATH
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["python_authority_paths"].append(bad_path)
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    commit = _commit(root, "bad manifest path")
    with pytest.raises(LockGenerationError, match="noncanonical"):
        generate_lock_bytes(root, expected_commit=commit)


def test_duplicate_manifest_path_is_rejected(tmp_path: Path):
    root, _ = _fixture_repo(tmp_path)
    manifest_path = root / MANIFEST_PATH
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["data_authority_paths"].append(manifest["python_authority_paths"][0])
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    commit = _commit(root, "duplicate path")
    with pytest.raises(LockGenerationError, match="duplicate authority path"):
        generate_lock_bytes(root, expected_commit=commit)


def test_missing_directory_and_symlink_authority_paths_are_rejected(tmp_path: Path):
    root, _ = _fixture_repo(tmp_path)
    manifest_path = root / MANIFEST_PATH
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    manifest["python_authority_paths"].append("scripts/missing.py")
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    commit = _commit(root, "missing path declared")
    with pytest.raises(LockGenerationError, match="missing"):
        generate_lock_bytes(root, expected_commit=commit)

    manifest["python_authority_paths"].remove("scripts/missing.py")
    (root / "scripts/directory.py").mkdir()
    manifest["python_authority_paths"].append("scripts/directory.py")
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    commit = _commit(root, "directory path declared")
    with pytest.raises(LockGenerationError, match="not a regular file"):
        generate_lock_bytes(root, expected_commit=commit)



def test_symlink_authority_path_is_rejected(tmp_path: Path):
    root, _ = _fixture_repo(tmp_path)
    target = root / "scripts/one.py"
    target.unlink()
    try:
        target.symlink_to(root / "contracts/one.json")
    except OSError:
        pytest.skip("Symlink creation is unavailable in this environment")
    commit = _commit(root, "symlink authority path")
    with pytest.raises(LockGenerationError, match="symlink"):
        generate_lock_bytes(root, expected_commit=commit)

def test_workflow_and_tests_have_no_dependency_identity_mirrors():
    root = LOCK_PATH.parent
    workflow = (root / ".github/workflows/validate.yml").read_text(encoding="utf-8")
    adapter_tests = (root / "tests/test_architect_adapter.py").read_text(encoding="utf-8")
    e2e_tests = (root / "tests/test_core_end_to_end.py").read_text(encoding="utf-8")
    literal_sha = re.compile(r"(?<![0-9a-f])[0-9a-f]{40}(?![0-9a-f])")
    assert not literal_sha.search(workflow)
    assert not literal_sha.search(adapter_tests)
    assert not literal_sha.search(e2e_tests)
    for text in (adapter_tests, e2e_tests):
        assert "EXPECTED_ARCHITECT_HEAD" not in text
        assert "EXPECTED_AUTHORITY_FILES" not in text
        assert not re.search(r"len\(connection\.identities\)\s*==\s*\d+", text)
