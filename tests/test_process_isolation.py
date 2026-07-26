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
    run_prefix_validation,
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

FORBIDDEN_PROCESS_ORIGINS = frozenset(
    {
        "os.fork",
        "os.forkpty",
        "multiprocessing",
        "multiprocessing.Process",
        "multiprocessing.Pool",
        "multiprocessing.get_context",
        "multiprocessing.set_start_method",
        "concurrent.futures.ProcessPoolExecutor",
        "importlib.reload",
    }
)


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


def _is_forbidden_origin(origin: str) -> bool:
    return origin in FORBIDDEN_PROCESS_ORIGINS or origin.startswith("multiprocessing.")


def _resolve_origins(
    node: ast.AST,
    bindings: dict[str, frozenset[str]],
) -> frozenset[str]:
    if isinstance(node, ast.Name):
        return bindings.get(node.id, frozenset())
    if isinstance(node, ast.Attribute):
        return frozenset(
            f"{parent}.{node.attr}"
            for parent in _resolve_origins(node.value, bindings)
        )
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "getattr"
        and len(node.args) == 2
        and not node.keywords
        and isinstance(node.args[1], ast.Constant)
        and isinstance(node.args[1].value, str)
    ):
        return frozenset(
            f"{parent}.{node.args[1].value}"
            for parent in _resolve_origins(node.args[0], bindings)
        )
    return frozenset()


def _merge_binding_environments(
    *environments: dict[str, frozenset[str]],
) -> dict[str, frozenset[str]]:
    names = set().union(*(environment.keys() for environment in environments))
    merged: dict[str, frozenset[str]] = {}
    for name in names:
        origins = frozenset().union(
            *(environment.get(name, frozenset()) for environment in environments)
        )
        if origins:
            merged[name] = origins
    return merged


def _star_import_forbidden_origins(module: str) -> tuple[str, ...]:
    if _is_forbidden_origin(module):
        return (module,)
    return tuple(
        origin
        for origin in sorted(FORBIDDEN_PROCESS_ORIGINS)
        if origin.rpartition(".")[0] == module
    )


def _forbidden_origin_violations(
    tree: ast.Module,
) -> list[tuple[str, str, int]]:
    violations: set[tuple[str, str, int]] = set()

    def record(origin: str, binding: str, node: ast.AST) -> None:
        violations.add((origin, binding, getattr(node, "lineno", 0)))

    def inspect_expression(
        node: ast.AST | None,
        bindings: dict[str, frozenset[str]],
    ) -> None:
        if node is None:
            return
        for candidate in ast.walk(node):
            if not isinstance(candidate, ast.Call):
                continue
            for origin in sorted(_resolve_origins(candidate.func, bindings)):
                if _is_forbidden_origin(origin):
                    record(
                        origin,
                        _dotted_name(candidate.func) or origin,
                        candidate,
                    )

    def bind_assignment(
        targets: list[ast.expr],
        value: ast.AST,
        bindings: dict[str, frozenset[str]],
    ) -> None:
        origins = _resolve_origins(value, bindings)
        for target in targets:
            if not isinstance(target, ast.Name):
                continue
            if origins:
                bindings[target.id] = origins
                for origin in sorted(origins):
                    if _is_forbidden_origin(origin):
                        record(origin, target.id, target)
            else:
                bindings.pop(target.id, None)

    def clear_target(
        target: ast.AST,
        bindings: dict[str, frozenset[str]],
    ) -> None:
        if isinstance(target, ast.Name):
            bindings.pop(target.id, None)
        elif isinstance(target, (ast.Tuple, ast.List)):
            for element in target.elts:
                clear_target(element, bindings)

    def nested_scope_bindings(
        bindings: dict[str, frozenset[str]],
        arguments: ast.arguments | None = None,
    ) -> dict[str, frozenset[str]]:
        nested = dict(bindings)
        if arguments is not None:
            for argument in (
                list(arguments.posonlyargs)
                + list(arguments.args)
                + list(arguments.kwonlyargs)
            ):
                nested.pop(argument.arg, None)
            if arguments.vararg is not None:
                nested.pop(arguments.vararg.arg, None)
            if arguments.kwarg is not None:
                nested.pop(arguments.kwarg.arg, None)
        return nested

    def process_statements(
        statements: list[ast.stmt],
        bindings: dict[str, frozenset[str]],
    ) -> dict[str, frozenset[str]]:
        environment = dict(bindings)
        for statement in statements:
            if isinstance(statement, ast.Import):
                for alias in statement.names:
                    if alias.asname:
                        local_name = alias.asname
                        canonical = alias.name
                    else:
                        local_name = alias.name.split(".", 1)[0]
                        canonical = local_name
                    environment[local_name] = frozenset({canonical})
                    if _is_forbidden_origin(alias.name):
                        record(alias.name, local_name, statement)
                continue

            if isinstance(statement, ast.ImportFrom):
                module = "." * statement.level + (statement.module or "")
                for alias in statement.names:
                    if alias.name == "*":
                        for origin in _star_import_forbidden_origins(module):
                            record(origin, f"{module}.*", statement)
                        continue
                    canonical = f"{module}.{alias.name}" if module else alias.name
                    local_name = alias.asname or alias.name
                    environment[local_name] = frozenset({canonical})
                    if _is_forbidden_origin(canonical):
                        record(canonical, local_name, statement)
                continue

            if isinstance(statement, ast.Assign):
                inspect_expression(statement.value, environment)
                bind_assignment(statement.targets, statement.value, environment)
                continue

            if isinstance(statement, ast.AnnAssign):
                inspect_expression(statement.annotation, environment)
                inspect_expression(statement.value, environment)
                if statement.value is not None:
                    bind_assignment([statement.target], statement.value, environment)
                else:
                    clear_target(statement.target, environment)
                continue

            if isinstance(statement, ast.AugAssign):
                inspect_expression(statement.target, environment)
                inspect_expression(statement.value, environment)
                clear_target(statement.target, environment)
                continue

            if isinstance(statement, ast.Expr):
                inspect_expression(statement.value, environment)
                continue

            if isinstance(statement, ast.Return):
                inspect_expression(statement.value, environment)
                continue

            if isinstance(statement, ast.Raise):
                inspect_expression(statement.exc, environment)
                inspect_expression(statement.cause, environment)
                continue

            if isinstance(statement, ast.Assert):
                inspect_expression(statement.test, environment)
                inspect_expression(statement.msg, environment)
                continue

            if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for decorator in statement.decorator_list:
                    inspect_expression(decorator, environment)
                for default in statement.args.defaults:
                    inspect_expression(default, environment)
                for default in statement.args.kw_defaults:
                    inspect_expression(default, environment)
                inspect_expression(statement.returns, environment)
                process_statements(
                    statement.body,
                    nested_scope_bindings(environment, statement.args),
                )
                environment.pop(statement.name, None)
                continue

            if isinstance(statement, ast.ClassDef):
                for decorator in statement.decorator_list:
                    inspect_expression(decorator, environment)
                for base in statement.bases:
                    inspect_expression(base, environment)
                for keyword in statement.keywords:
                    inspect_expression(keyword.value, environment)
                process_statements(
                    statement.body,
                    nested_scope_bindings(environment),
                )
                environment.pop(statement.name, None)
                continue

            if isinstance(statement, ast.If):
                inspect_expression(statement.test, environment)
                body_environment = process_statements(statement.body, environment)
                else_environment = process_statements(statement.orelse, environment)
                environment = _merge_binding_environments(
                    body_environment,
                    else_environment,
                )
                continue

            if isinstance(statement, (ast.For, ast.AsyncFor)):
                inspect_expression(statement.iter, environment)
                body_start = dict(environment)
                clear_target(statement.target, body_start)
                body_environment = process_statements(statement.body, body_start)
                else_environment = process_statements(statement.orelse, environment)
                environment = _merge_binding_environments(
                    environment,
                    body_environment,
                    else_environment,
                )
                continue

            if isinstance(statement, ast.While):
                inspect_expression(statement.test, environment)
                body_environment = process_statements(statement.body, environment)
                else_environment = process_statements(statement.orelse, environment)
                environment = _merge_binding_environments(
                    environment,
                    body_environment,
                    else_environment,
                )
                continue

            if isinstance(statement, (ast.With, ast.AsyncWith)):
                body_start = dict(environment)
                for item in statement.items:
                    inspect_expression(item.context_expr, environment)
                    if item.optional_vars is not None:
                        clear_target(item.optional_vars, body_start)
                body_environment = process_statements(statement.body, body_start)
                environment = _merge_binding_environments(
                    environment,
                    body_environment,
                )
                continue

            if isinstance(statement, (ast.Try, ast.TryStar)):
                body_environment = process_statements(statement.body, environment)
                normal_environment = process_statements(
                    statement.orelse,
                    body_environment,
                )
                branch_environments = [normal_environment]
                for handler in statement.handlers:
                    inspect_expression(handler.type, environment)
                    handler_start = dict(environment)
                    if handler.name:
                        handler_start.pop(handler.name, None)
                    branch_environments.append(
                        process_statements(handler.body, handler_start)
                    )
                merged = _merge_binding_environments(*branch_environments)
                environment = process_statements(statement.finalbody, merged)
                continue

            if isinstance(statement, ast.Match):
                inspect_expression(statement.subject, environment)
                case_environments = [dict(environment)]
                for case in statement.cases:
                    inspect_expression(case.guard, environment)
                    case_environments.append(
                        process_statements(case.body, environment)
                    )
                environment = _merge_binding_environments(*case_environments)
                continue

            if isinstance(statement, ast.Delete):
                for target in statement.targets:
                    clear_target(target, environment)
                continue

            for child in ast.iter_child_nodes(statement):
                if isinstance(child, ast.expr):
                    inspect_expression(child, environment)

        return environment

    process_statements(tree.body, {})
    return sorted(violations)


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


@pytest.mark.parametrize(
    ("source", "expected_origins"),
    [
        ("from os import fork\nfork()\n", {"os.fork"}),
        ("from os import fork as clone\nclone()\n", {"os.fork"}),
        ("import os as platform\nplatform.fork()\n", {"os.fork"}),
        (
            "import os as platform\nclone = platform.fork\nclone()\n",
            {"os.fork"},
        ),
        ("from os import *\nfork()\n", {"os.fork", "os.forkpty"}),
        (
            'import os as platform\ngetattr(platform, "fork")()\n',
            {"os.fork"},
        ),
        (
            "import importlib as loader\nloader.reload(module)\n",
            {"importlib.reload"},
        ),
        (
            "from importlib import reload as refresh\nrefresh(module)\n",
            {"importlib.reload"},
        ),
        (
            "import concurrent.futures as cf\ncf.ProcessPoolExecutor()\n",
            {"concurrent.futures.ProcessPoolExecutor"},
        ),
        (
            "from multiprocessing import Process as Worker\nWorker()\n",
            {"multiprocessing.Process"},
        ),
    ],
)
def test_binding_aware_guard_rejects_forbidden_origin_aliases(
    source: str,
    expected_origins: set[str],
):
    violations = _forbidden_origin_violations(ast.parse(source))

    assert violations == sorted(violations)
    assert expected_origins <= {origin for origin, _, _ in violations}


@pytest.mark.parametrize(
    "source",
    [
        "import os as platform\nplatform.getpid()\n",
        "import concurrent.futures as cf\ncf.ThreadPoolExecutor()\n",
        (
            "class LocalObject:\n"
            "    def reload(self):\n"
            "        return None\n"
            "LocalObject().reload()\n"
        ),
    ],
)
def test_binding_aware_guard_accepts_harmless_controls(source: str):
    assert _forbidden_origin_violations(ast.parse(source)) == []


@pytest.mark.parametrize(
    ("source", "expected_origin"),
    [
        (
            "import os as platform\n"
            "platform.fork()\n"
            "import harmless as platform\n",
            "os.fork",
        ),
        (
            "import os as platform\n"
            "platform.forkpty()\n"
            "import harmless as platform\n",
            "os.forkpty",
        ),
        (
            "import concurrent.futures as cf\n"
            "cf.ProcessPoolExecutor()\n"
            "import harmless as cf\n",
            "concurrent.futures.ProcessPoolExecutor",
        ),
        (
            "import importlib as loader\n"
            "loader.reload(module)\n"
            "import harmless as loader\n",
            "importlib.reload",
        ),
        (
            "import os as platform\n"
            "clone = platform.fork\n"
            "import harmless as platform\n"
            "clone()\n",
            "os.fork",
        ),
    ],
)
def test_binding_aware_guard_uses_source_order_for_calls_and_assignments(
    source: str,
    expected_origin: str,
):
    violations = _forbidden_origin_violations(ast.parse(source))

    assert expected_origin in {origin for origin, _, _ in violations}


def test_binding_aware_guard_applies_rebind_before_call():
    source = (
        "import os as platform\n"
        "import harmless as platform\n"
        "platform.fork()\n"
    )

    assert _forbidden_origin_violations(ast.parse(source)) == []


def test_binding_aware_guard_merges_branch_origins_conservatively():
    source = (
        "import harmless as platform\n"
        "if condition:\n"
        "    import os as platform\n"
        "platform.fork()\n"
    )

    violations = _forbidden_origin_violations(ast.parse(source))

    assert "os.fork" in {origin for origin, _, _ in violations}


@pytest.mark.parametrize(
    ("source", "expected_origin"),
    [
        ("from multiprocessing import *\n", "multiprocessing"),
        (
            "from multiprocessing.context import *\n",
            "multiprocessing.context",
        ),
        ("from multiprocessing.pool import *\n", "multiprocessing.pool"),
    ],
)
def test_binding_aware_guard_rejects_multiprocessing_star_imports(
    source: str,
    expected_origin: str,
):
    violations = _forbidden_origin_violations(ast.parse(source))

    assert violations == [(expected_origin, f"{expected_origin}.*", 1)]


@pytest.mark.parametrize(
    "source",
    [
        "from os.path import *\n",
        "from concurrent.futures.thread import *\n",
    ],
)
def test_binding_aware_guard_accepts_harmless_star_imports(source: str):
    assert _forbidden_origin_violations(ast.parse(source)) == []


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

    for relative, tree in (
        ("src/ev4_architect_stage_qc/process_launcher.py", launcher_tree),
        ("src/ev4_architect_stage_qc/process_child.py", child_tree),
    ):
        violations = _forbidden_origin_violations(tree)
        assert not violations, f"{relative}: forbidden origins: {violations}"

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
        "_prefix": "core",
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
    prefix = run_prefix_validation(folder, root, source_kind="fixture")
    prefinal = run_prefinal_validation(folder, root, source_kind="fixture")
    final = run_final_validation(
        folder,
        terminal_path,
        root,
        source_kind="fixture",
    )
    second_verify = verify_connection(root)

    assert first_verify.ok and second_verify.ok
    assert prefix.success and prefix.code == "PREFIX_VALID"
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
        prefix.child_pid,
        prefinal.child_pid,
        final.child_pid,
        second_verify.child_pid,
    }
    assert None not in pids
    assert os.getpid() not in pids
    assert len(pids) == 5
    for result in (first_verify, prefix, prefinal, final, second_verify):
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


@pytest.mark.parametrize(
    "field",
    [
        "stage_results",
        "run_state",
        "payload",
        "provenance",
        "continuation_authority",
        "handoff_allowed",
    ],
)
def test_prefix_child_protocol_rejects_caller_authority_fields(
    tmp_path: Path,
    field: str,
):
    request_path = tmp_path / "request.json"
    result_path = tmp_path / "result.json"
    request = {
        "protocol_version": launcher.PROTOCOL_VERSION,
        "request_id": f"forbidden-prefix-{field}",
        "operation": "run_prefix_validation",
        "architect_repository_path": str(authority_root()),
        "stage_output_folder": str(tmp_path / "stages"),
        "source_kind": "fixture",
        field: {"forbidden": True},
    }
    write_json(request_path, request)
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
