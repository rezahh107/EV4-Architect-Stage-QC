from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import ev4_architect_stage_qc.process_launcher as launcher
from ev4_architect_stage_qc.process_launcher import (
    run_final_validation,
    run_prefinal_validation,
    verify_connection,
)

RUNTIME_MODULES = {
    "ev4_official_runtime",
    "architect_quality_runtime",
    "architect_quality_runtime.history",
    "architect_project_gate_finalization",
    "_ev4_architect_quality_runtime_internal",
}


def authority_root() -> Path:
    value = os.environ.get("EV4_ARCHITECT_REPO")
    if not value:
        pytest.skip("integration-only: set EV4_ARCHITECT_REPO to the locked checkout")
    return Path(value).resolve()


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: dict) -> None:
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def architect_outputs(root: Path) -> tuple[list[dict], dict]:
    prefinal = [
        read_json(path)
        for path in sorted(
            (root / "fixtures/conversational-run/valid/minimal-complete-run").glob(
                "*.json"
            )
        )
    ]
    terminal = read_json(
        root
        / "fixtures/conversational-run/valid/terminal/project-gate-export.json"
    )
    assert len(prefinal) == 11
    return prefinal, terminal


def stage_folder(base: Path, outputs: list[dict]) -> Path:
    folder = base / "stages"
    folder.mkdir(parents=True)
    for index, value in enumerate(outputs, 1):
        write_json(folder / f"{index:02d}.json", value)
    return folder


def terminal_file(base: Path, value: dict) -> Path:
    base.mkdir(parents=True, exist_ok=True)
    path = base / "terminal.json"
    write_json(path, value)
    return path


def _origin_paths(result) -> list[Path]:
    identity = result.execution_identity
    assert identity is not None
    paths = [
        identity.wrapper_origin,
        identity.package_origin,
        identity.history_origin,
        identity.finalization_origin,
    ]
    assert all(path is not None for path in paths)
    return [path for path in paths if path is not None]


def _origin_paths_allow_none(result) -> list[Path | None]:
    identity = result.execution_identity
    if identity is None:
        return [None, None, None, None]
    return [
        identity.wrapper_origin,
        identity.package_origin,
        identity.history_origin,
        identity.finalization_origin,
    ]


def _assert_origins(result, root: Path) -> None:
    expected = {
        (root / "scripts/architect_quality_runtime.py").resolve(),
        (root / "scripts/architect_quality_runtime/__init__.py").resolve(),
        (root / "scripts/architect_quality_runtime/history.py").resolve(),
        (root / "scripts/architect_project_gate_finalization.py").resolve(),
    }
    assert set(_origin_paths(result)) == expected
    for path in expected:
        path.relative_to(root.resolve())


def _git(root: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(root), *args],
        text=True,
        stderr=subprocess.DEVNULL,
    ).strip()


def _worktree(root: Path, destination: Path, *, branch: str | None = None) -> None:
    args = ["worktree", "add"]
    if branch:
        args.extend(["-b", branch])
    else:
        args.append("--detach")
    args.extend([str(destination), "HEAD"])
    _git(root, *args)
    _git(destination, "config", "core.autocrlf", "false")
    _git(destination, "config", "core.eol", "lf")
    _git(destination, "reset", "--hard", "HEAD")
    if branch:
        _git(destination, "config", "user.email", "qc@example.invalid")
        _git(destination, "config", "user.name", "QC process isolation test")


def _remove_worktree(root: Path, destination: Path, branch: str | None = None) -> None:
    _git(root, "worktree", "remove", "--force", str(destination))
    if branch:
        _git(root, "branch", "-D", branch)


def test_parent_has_no_direct_runtime_boundary_and_loads_no_runtime_modules():
    repository_root = Path(__file__).resolve().parents[1]
    for relative in (
        "src/ev4_architect_stage_qc/app.py",
        "src/ev4_architect_stage_qc/process_launcher.py",
    ):
        tree = ast.parse((repository_root / relative).read_text(encoding="utf-8"))
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
        assert not any("architect_adapter" in name for name in imported)
        assert not any(name.endswith(".core") for name in imported)
        assert not any("architect_quality_runtime" in name for name in imported)

    before = set(sys.modules) & RUNTIME_MODULES
    result = verify_connection(authority_root())
    after = set(sys.modules) & RUNTIME_MODULES

    assert result.ok, result.reason
    assert after == before
    assert result.child_pid != os.getpid()
    _assert_origins(result, authority_root())


def test_every_operation_uses_a_fresh_child_and_preserves_valid_results(
    tmp_path: Path,
):
    root = authority_root()
    outputs, terminal = architect_outputs(root)
    folder = stage_folder(tmp_path, outputs)
    terminal_path = terminal_file(tmp_path, terminal)

    first_verify = verify_connection(root)
    prefinal = run_prefinal_validation(folder, root, source_kind="fixture")
    final = run_final_validation(
        folder,
        terminal_path,
        root,
        source_kind="fixture",
    )
    second_verify = verify_connection(root)

    assert first_verify.ok and second_verify.ok
    assert prefinal.success and prefinal.code == "PREFINAL_VALID"
    assert not final.success and final.code == "FINAL_HANDOFF_BLOCKED"
    assert prefinal.attempt_path is not None
    assert final.attempt_path is not None
    assert (
        final.attempt_path / "generated-artifacts/architect-project-gate.json"
    ).is_file()
    assert (
        final.attempt_path
        / "generated-artifacts/architect-project-gate-receipt.json"
    ).is_file()

    pids = {
        first_verify.child_pid,
        prefinal.child_pid,
        final.child_pid,
        second_verify.child_pid,
    }
    assert None not in pids
    assert os.getpid() not in pids
    assert len(pids) == 4
    for result in (first_verify, prefinal, final, second_verify):
        _assert_origins(result, root)


def test_checkout_switch_a_b_a_reports_selected_checkout_origins(tmp_path: Path):
    root = authority_root()
    checkout_b = tmp_path / "architect-b"
    _worktree(root, checkout_b)
    try:
        first_a = verify_connection(root)
        observed_b = verify_connection(checkout_b)
        second_a = verify_connection(root)

        assert first_a.ok and observed_b.ok and second_a.ok
        _assert_origins(first_a, root)
        _assert_origins(observed_b, checkout_b)
        _assert_origins(second_a, root)
        assert len({first_a.child_pid, observed_b.child_pid, second_a.child_pid}) == 3
    finally:
        _remove_worktree(root, checkout_b)


def _write_fake_result(command: list[str], value: object) -> None:
    result_path = Path(command[command.index("--result") + 1])
    if isinstance(value, str):
        result_path.write_text(value, encoding="utf-8")
    else:
        result_path.write_text(
            json.dumps(value, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )


def _completed_verify_result(command: list[str]) -> dict:
    request_path = Path(command[command.index("--request") + 1])
    request = read_json(request_path)
    return {
        "protocol_version": launcher.PROTOCOL_VERSION,
        "request_id": request["request_id"],
        "operation": request["operation"],
        "child_pid": 12345,
        "status": "completed",
        "execution_identity": {
            "wrapper_origin": None,
            "package_origin": None,
            "history_origin": None,
            "finalization_origin": None,
        },
        "connection_ok": True,
        "connection_reason": "ok",
        "actual_commit": "a" * 40,
        "reference_commit": "a" * 40,
        "runtime_interface_id": "ev4-architect-quality-runtime@2.0.0",
        "compatibility_mode": "authority_file_identity",
        "authority_file_count": 32,
        "ref": "fixture",
    }


@pytest.mark.parametrize(
    ("mode", "expected_code"),
    [
        ("startup", "CHILD_STARTUP_FAILED"),
        ("nonzero", "CHILD_NONZERO_EXIT"),
        ("missing", "CHILD_RESULT_MISSING"),
        ("malformed", "CHILD_RESULT_MALFORMED"),
        ("request-mismatch", "CHILD_RESULT_IDENTITY_MISMATCH"),
        ("unknown-status", "CHILD_RESULT_STATUS_INVALID"),
    ],
)
def test_child_failures_are_bounded_and_never_reuse_prior_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
    expected_code: str,
):
    calls = 0

    def fake_run(command, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            value = _completed_verify_result(command)
            _write_fake_result(command, value)
            return SimpleNamespace(returncode=0)
        if mode == "startup":
            raise OSError("controlled startup failure")
        if mode == "nonzero":
            return SimpleNamespace(returncode=7)
        if mode == "missing":
            return SimpleNamespace(returncode=0)
        if mode == "malformed":
            _write_fake_result(command, "{")
            return SimpleNamespace(returncode=0)
        value = _completed_verify_result(command)
        if mode == "request-mismatch":
            value["request_id"] = "wrong-request"
        elif mode == "unknown-status":
            value["status"] = "unknown"
        _write_fake_result(command, value)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(launcher.subprocess, "run", fake_run)
    first = verify_connection(tmp_path)
    second = verify_connection(tmp_path)

    assert first.ok
    assert not second.ok
    assert second.commit is None
    assert expected_code in second.reason


def test_child_rejects_forbidden_request_fields(tmp_path: Path):
    request_path = tmp_path / "request.json"
    result_path = tmp_path / "result.json"
    write_json(
        request_path,
        {
            "protocol_version": launcher.PROTOCOL_VERSION,
            "request_id": "forbidden-request",
            "operation": "verify_connection",
            "architect_repository_path": str(authority_root()),
            "payload": {"forbidden": True},
        },
    )
    environment = os.environ.copy()
    source_root = str(Path(__file__).resolve().parents[1] / "src")
    environment["PYTHONPATH"] = source_root
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            launcher.CHILD_MODULE,
            "--request",
            str(request_path),
            "--result",
            str(result_path),
        ],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0
    result = read_json(result_path)
    assert result["status"] == "error"
    assert result["error_code"] == "MALFORMED_REQUEST"
    assert result["child_pid"] != os.getpid()


def test_reference_and_candidate_identity_predicates_remain_distinct(
    tmp_path: Path,
):
    root = authority_root()
    exact = verify_connection(root)
    assert exact.ok, exact.reason
    assert exact.commit == exact.reference_commit

    compatible = tmp_path / "compatible"
    compatible_branch = f"qc-compatible-{tmp_path.name}"
    _worktree(root, compatible, branch=compatible_branch)
    try:
        document = compatible / "docs/process-isolation-compatible.txt"
        document.parent.mkdir(exist_ok=True)
        document.write_text("non-authority change\n", encoding="utf-8")
        _git(compatible, "add", str(document.relative_to(compatible)))
        _git(compatible, "commit", "-m", "test: compatible non-authority commit")
        compatible_result = verify_connection(compatible)
        assert compatible_result.ok, compatible_result.reason
        assert compatible_result.commit != compatible_result.reference_commit
        _assert_origins(compatible_result, compatible)
    finally:
        _remove_worktree(root, compatible, compatible_branch)

    changed = tmp_path / "changed"
    changed_branch = f"qc-changed-{tmp_path.name}"
    _worktree(root, changed, branch=changed_branch)
    try:
        target = changed / "scripts/architect_quality_runtime.py"
        target.write_bytes(target.read_bytes() + b"\n# changed authority\n")
        _git(changed, "add", "scripts/architect_quality_runtime.py")
        _git(changed, "commit", "-m", "test: changed authority blob")
        changed_result = verify_connection(changed)
        assert not changed_result.ok
        assert "Authority lock mismatch for committed blob" in changed_result.reason
        assert changed_result.execution_identity is not None
        assert all(path is None for path in _origin_paths_allow_none(changed_result))
    finally:
        _remove_worktree(root, changed, changed_branch)
