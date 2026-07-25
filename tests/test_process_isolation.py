from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

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


def _dotted_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _dotted_name(node.value)
        return f"{parent}.{node.attr}" if parent else node.attr
    return ""


def _function(tree: ast.Module, name: str) -> ast.FunctionDef:
    matches = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == name
    ]
    assert len(matches) == 1
    return matches[0]


def test_real_launcher_executes_exactly_one_new_python_interpreter(
    monkeypatch: pytest.MonkeyPatch,
):
    root = authority_root()
    original_run = launcher.subprocess.run
    invocations: list[tuple[list[str], dict]] = []

    def recording_run(command, **kwargs):
        captured = list(command)
        assert captured.count("--request") == 1
        assert captured.count("--result") == 1
        request_path = Path(captured[captured.index("--request") + 1])
        result_path = Path(captured[captured.index("--result") + 1])
        assert request_path.is_file()
        assert not result_path.exists()
        request = read_json(request_path)
        assert request["operation"] == "verify_connection"
        invocations.append((captured, dict(kwargs)))
        return original_run(command, **kwargs)

    monkeypatch.setattr(launcher.subprocess, "run", recording_run)
    result = verify_connection(root)

    assert result.ok, result.reason
    assert len(invocations) == 1
    command, kwargs = invocations[0]
    assert Path(command[0]).resolve() == Path(sys.executable).resolve()
    assert command[1:3] == ["-m", launcher.CHILD_MODULE]
    assert kwargs.get("shell", False) is False
    assert result.child_pid != os.getpid()
    _assert_origins(result, root)


def test_preloaded_parent_runtime_sentinels_are_not_inherited_by_child(
    tmp_path: Path,
):
    root = authority_root()
    absent = object()
    saved = {name: sys.modules.get(name, absent) for name in RUNTIME_MODULES}
    fake_root = tmp_path / "parent-runtime-sentinels"
    fake_root.mkdir()
    sentinels: dict[str, ModuleType] = {}

    try:
        for name in sorted(RUNTIME_MODULES):
            module = ModuleType(name)
            module.__file__ = str(fake_root / f"{name.replace('.', '_')}.py")
            module.__package__ = name.rpartition(".")[0]
            if name == "architect_quality_runtime":
                module.__path__ = [str(fake_root / "architect_quality_runtime")]
            sentinels[name] = module
            sys.modules[name] = module
        setattr(
            sentinels["architect_quality_runtime"],
            "history",
            sentinels["architect_quality_runtime.history"],
        )

        result = verify_connection(root)

        assert result.ok, result.reason
        assert result.child_pid != os.getpid()
        _assert_origins(result, root)
        fake_origins = {Path(module.__file__).resolve() for module in sentinels.values()}
        assert fake_origins.isdisjoint(set(_origin_paths(result)))
        for name, module in sentinels.items():
            assert sys.modules.get(name) is module
    finally:
        for name, previous in saved.items():
            if previous is absent:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous


def test_repository_locks_fresh_interpreter_creation_and_child_import_timing():
    repository_root = Path(__file__).resolve().parents[1]
    launcher_tree = ast.parse(
        (
            repository_root
            / "src/ev4_architect_stage_qc/process_launcher.py"
        ).read_text(encoding="utf-8")
    )
    child_tree = ast.parse(
        (
            repository_root
            / "src/ev4_architect_stage_qc/process_child.py"
        ).read_text(encoding="utf-8")
    )

    imported_names: set[str] = set()
    for tree in (launcher_tree, child_tree):
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_names.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported_names.add(node.module or "")
                imported_names.update(alias.name for alias in node.names)

    assert not any(
        name == "multiprocessing" or name.startswith("multiprocessing.")
        for name in imported_names
    )
    assert "ProcessPoolExecutor" not in imported_names

    forbidden_calls = {
        "os.fork",
        "os.forkpty",
        "multiprocessing.Process",
        "multiprocessing.Pool",
        "multiprocessing.get_context",
        "multiprocessing.set_start_method",
        "Process",
        "Pool",
        "ProcessPoolExecutor",
        "importlib.reload",
        "reload",
    }
    observed_calls = {
        _dotted_name(node.func)
        for tree in (launcher_tree, child_tree)
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
    }
    assert forbidden_calls.isdisjoint(observed_calls)

    for tree in (launcher_tree, child_tree):
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if (
                    _dotted_name(node.func.value) == "sys.modules"
                    and node.func.attr
                    in {"clear", "pop", "popitem", "setdefault", "update"}
                ):
                    pytest.fail("production must not mutate governed sys.modules state")
            targets: list[ast.AST] = []
            if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
                raw_targets = (
                    node.targets if isinstance(node, ast.Assign) else [node.target]
                )
                targets.extend(raw_targets)
            elif isinstance(node, ast.Delete):
                targets.extend(node.targets)
            for target in targets:
                if (
                    isinstance(target, ast.Subscript)
                    and _dotted_name(target.value) == "sys.modules"
                ) or (
                    isinstance(target, ast.Attribute)
                    and _dotted_name(target) == "sys.modules"
                ):
                    pytest.fail("production must not replace or delete sys.modules state")

    invoke = _function(launcher_tree, "_invoke")
    subprocess_runs = [
        node
        for node in ast.walk(invoke)
        if isinstance(node, ast.Call)
        and _dotted_name(node.func) == "subprocess.run"
    ]
    assert len(subprocess_runs) == 1
    assert not any(isinstance(node, ast.While) for node in ast.walk(invoke))

    main = _function(child_tree, "main")
    execute_calls = [
        node
        for node in ast.walk(main)
        if isinstance(node, ast.Call) and _dotted_name(node.func) == "_execute"
    ]
    assert len(execute_calls) == 1
    assert not any(isinstance(node, ast.While) for node in ast.walk(main))

    top_level_imports: set[str] = set()
    for node in child_tree.body:
        if isinstance(node, ast.Import):
            top_level_imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            top_level_imports.add(node.module or "")
    assert not any("architect_adapter" in name for name in top_level_imports)
    assert not any(name.endswith(".core") or name == "core" for name in top_level_imports)
    assert not any("architect_quality_runtime" in name for name in top_level_imports)

    expected_local_imports = {
        "_verify": "architect_adapter",
        "_prefinal": "core",
        "_final": "core",
    }
    for function_name, required_fragment in expected_local_imports.items():
        function = _function(child_tree, function_name)
        local_imports = {
            node.module or ""
            for node in ast.walk(function)
            if isinstance(node, ast.ImportFrom)
        }
        assert any(required_fragment in name for name in local_imports)


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
