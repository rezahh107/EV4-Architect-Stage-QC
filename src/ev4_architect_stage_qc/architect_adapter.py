from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from .lock_generator import LOCK_SCHEMA_VERSION, canonical_lock_bytes

LOCK_PATH = Path(__file__).resolve().parents[2] / "architect-authority.lock.json"
MANIFEST_PATH = "manifests/architect-runtime-authority-manifest.v1.json"
FINALIZATION_ENTRYPOINT = "scripts/architect_quality_runtime.py#finalize_project_gate"
SHA1_RE = re.compile(r"^[0-9a-f]{40}$")


@dataclass(frozen=True)
class Connection:
    ok: bool
    path: Path
    commit: str | None
    reason: str
    identities: dict[str, str]
    runtime: object | None = None
    reference_commit: str | None = None
    compatibility_mode: str | None = None
    ref: str | None = None
    repository_identity: str | None = None
    runtime_interface_id: str | None = None


def sibling_checkout() -> Path | None:
    path = Path.cwd().parent / "EV4-Architect-Repo"
    return path if path.is_dir() else None


def _git_text(root: Path, *args: str) -> str | None:
    try:
        return subprocess.check_output(
            ["git", "-C", str(root), *args],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:
        return None


def _git_bytes(root: Path, *args: str) -> bytes | None:
    try:
        return subprocess.check_output(
            ["git", "-C", str(root), *args],
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        return None


def _authority_attributes(root: Path, rel: str) -> dict[str, str] | None:
    values = _git_bytes(
        root,
        "check-attr",
        "-z",
        "filter",
        "working-tree-encoding",
        "ident",
        "--",
        rel,
    )
    if values is None:
        return None
    fields = values.decode("utf-8", "surrogateescape").split("\0")
    if fields[-1] != "" or len(fields) != 10:
        return None
    return dict(zip(fields[1::3], fields[2::3], strict=True))


def _committed_blob_bytes(root: Path, oid: str) -> bytes | None:
    return _git_bytes(root, "cat-file", "blob", oid)


def _working_tree_matches_blob(root: Path, rel: str, committed: bytes) -> bool:
    try:
        return (root / rel).read_bytes() == committed
    except OSError:
        return False


def _identity(root: Path) -> str | None:
    remote = _git_text(root, "config", "--get", "remote.origin.url") or ""
    normalized = remote.removesuffix(".git").removesuffix("/")
    if normalized.startswith("git@github.com:"):
        normalized = "https://github.com/" + normalized.removeprefix("git@github.com:")
    elif normalized.startswith("ssh://git@github.com/"):
        normalized = "https://github.com/" + normalized.removeprefix("ssh://git@github.com/")
    if normalized.casefold() == "https://github.com/rezahh107/ev4-architect-repo":
        return "rezahh107/ev4-architect-repo"
    return None


def _failed(
    root: Path,
    commit: str | None,
    reason: str,
    identities: dict[str, str],
    lock: dict[str, Any] | None = None,
    ref: str | None = None,
    repository_identity: str | None = None,
) -> Connection:
    lock = lock or {}
    return Connection(
        False,
        root,
        commit,
        reason,
        dict(identities),
        reference_commit=lock.get("reference_commit_sha"),
        compatibility_mode=lock.get("compatibility_mode"),
        ref=ref,
        repository_identity=repository_identity,
        runtime_interface_id=lock.get("runtime_interface_id"),
    )


def _canonical_lock_path(value: Any) -> bool:
    if not isinstance(value, str) or not value or "\\" in value:
        return False
    path = PurePosixPath(value)
    return (
        not path.is_absolute()
        and str(path) == value
        and bool(path.parts)
        and all(part not in {"", ".", ".."} for part in path.parts)
        and ":" not in path.parts[0]
    )


def _safe_attributes(root: Path, rel: str) -> str | None:
    attributes = _authority_attributes(root, rel)
    if attributes is None:
        return f"Authority Git attributes cannot be verified: {rel}"
    for attribute, value in sorted(attributes.items()):
        if value not in {"unspecified", "unset"}:
            return f"Unsafe authority Git attribute: {rel}: {attribute}={value}"
    return None


def _manifest_inventory(manifest: Any) -> tuple[list[str] | None, str | None]:
    if not isinstance(manifest, dict):
        return None, "Runtime Authority Manifest must be a JSON object."
    values: list[str] = []
    seen: set[str] = set()
    for key in ("python_authority_paths", "data_authority_paths"):
        paths = manifest.get(key)
        if not isinstance(paths, list):
            return None, f"Runtime Authority Manifest inventory is malformed: {key}"
        for path in paths:
            if not _canonical_lock_path(path):
                return None, f"Runtime Authority Manifest inventory is malformed: {key}"
            if path in seen:
                return None, f"Runtime Authority Manifest contains duplicate authority path: {path}"
            seen.add(path)
            values.append(path)
    if MANIFEST_PATH in seen:
        return None, f"Runtime Authority Manifest contains duplicate authority path: {MANIFEST_PATH}"
    values.append(MANIFEST_PATH)
    return sorted(values), None


def _verify_locked_path(
    root: Path,
    commit: str,
    rel: str,
    expected_oid: str,
    identities: dict[str, str],
) -> tuple[bytes | None, str | None]:
    if not _canonical_lock_path(rel) or not SHA1_RE.fullmatch(expected_oid):
        return None, "QC authority compatibility lock file inventory is malformed."
    path = root / rel
    if not path.is_file() or path.is_symlink():
        return None, f"Required authority working-tree file missing: {rel}"
    attribute_error = _safe_attributes(root, rel)
    if attribute_error:
        return None, attribute_error
    committed_oid = _git_text(root, "rev-parse", f"{commit}:{rel}")
    if not committed_oid:
        return None, f"Required authority file missing from current commit: {rel}"
    identities[rel] = committed_oid
    if committed_oid != expected_oid:
        return None, f"Authority lock mismatch for committed blob: {rel}"
    committed = _committed_blob_bytes(root, committed_oid)
    if committed is None:
        return None, f"Committed authority blob unavailable: {rel}"
    if not _working_tree_matches_blob(root, rel, committed):
        return None, f"Authority working-tree bytes differ from committed blob: {rel}"
    return committed, None


def verify(root: Path, lock_path: Path | None = None) -> Connection:
    root = Path(root).expanduser().resolve()
    identities: dict[str, str] = {}
    lock_path = lock_path or LOCK_PATH
    try:
        lock_bytes = lock_path.read_bytes()
        lock = json.loads(lock_bytes.decode("utf-8"))
    except Exception:
        return _failed(
            root,
            _git_text(root, "rev-parse", "HEAD"),
            "QC authority compatibility lock is unavailable.",
            identities,
        )
    if not isinstance(lock, dict):
        return _failed(root, None, "QC authority compatibility lock is malformed.", identities)
    if canonical_lock_bytes(lock) != lock_bytes:
        return _failed(
            root,
            _git_text(root, "rev-parse", "HEAD"),
            "QC authority compatibility lock is noncanonical.",
            identities,
            lock,
        )
    if lock.get("schema_version") != LOCK_SCHEMA_VERSION:
        return _failed(
            root,
            _git_text(root, "rev-parse", "HEAD"),
            "QC authority compatibility lock uses an unsupported schema version.",
            identities,
            lock,
        )

    commit = _git_text(root, "rev-parse", "HEAD")
    ref = _git_text(root, "symbolic-ref", "--short", "HEAD") or "detached-compatible-head"
    if not commit:
        return _failed(
            root,
            None,
            "Selected Architect folder is not a Git checkout.",
            identities,
            lock,
            ref,
        )
    if lock.get("compatibility_mode") != "authority_file_identity":
        return _failed(
            root,
            commit,
            "QC authority compatibility lock uses an unsupported compatibility mode.",
            identities,
            lock,
            ref,
        )
    if lock.get("identity_algorithm") != "git_blob_oid_sha1":
        return _failed(
            root,
            commit,
            "QC authority compatibility lock uses an unsupported file identity algorithm.",
            identities,
            lock,
            ref,
        )
    reference_commit = lock.get("reference_commit_sha")
    if not isinstance(reference_commit, str) or not SHA1_RE.fullmatch(reference_commit):
        return _failed(
            root,
            commit,
            "QC authority compatibility lock reference commit is malformed.",
            identities,
            lock,
            ref,
        )
    entry_points = lock.get("entry_points")
    if not isinstance(entry_points, list) or FINALIZATION_ENTRYPOINT not in entry_points:
        return _failed(
            root,
            commit,
            "QC authority compatibility lock does not declare the Runtime finalization entry point.",
            identities,
            lock,
            ref,
        )

    repository_identity = _identity(root)
    if repository_identity != str(lock.get("repository", "")).casefold():
        return _failed(
            root,
            commit,
            "Selected checkout identity is not rezahh107/EV4-Architect-Repo.",
            identities,
            lock,
            ref,
        )

    files = lock.get("files")
    if not isinstance(files, dict) or not files:
        return _failed(
            root,
            commit,
            "QC authority compatibility lock has no file inventory.",
            identities,
            lock,
            ref,
            repository_identity,
        )
    if MANIFEST_PATH not in files:
        return _failed(
            root,
            commit,
            f"Authority Lock is missing Manifest path: {MANIFEST_PATH}",
            identities,
            lock,
            ref,
            repository_identity,
        )

    manifest_bytes, error = _verify_locked_path(
        root,
        commit,
        MANIFEST_PATH,
        files[MANIFEST_PATH],
        identities,
    )
    if error or manifest_bytes is None:
        return _failed(
            root,
            commit,
            error or "Runtime Authority Manifest verification failed.",
            identities,
            lock,
            ref,
            repository_identity,
        )
    try:
        manifest = json.loads(manifest_bytes.decode("utf-8"))
    except Exception:
        return _failed(
            root,
            commit,
            "Runtime Authority Manifest is not valid UTF-8 JSON.",
            identities,
            lock,
            ref,
            repository_identity,
        )
    if manifest.get("owner_repository", "").casefold() != str(lock.get("repository", "")).casefold():
        return _failed(
            root,
            commit,
            "Runtime Authority Manifest repository identity mismatch.",
            identities,
            lock,
            ref,
            repository_identity,
        )
    if manifest.get("runtime_interface_id") != lock.get("runtime_interface_id"):
        return _failed(
            root,
            commit,
            "Runtime Authority Manifest interface identity mismatch.",
            identities,
            lock,
            ref,
            repository_identity,
        )
    expected_paths, inventory_error = _manifest_inventory(manifest)
    if inventory_error or expected_paths is None:
        return _failed(
            root,
            commit,
            inventory_error or "Runtime Authority Manifest inventory is malformed.",
            identities,
            lock,
            ref,
            repository_identity,
        )

    expected_set = set(expected_paths)
    lock_set = set(files)
    missing = sorted(expected_set - lock_set)
    if missing:
        return _failed(
            root,
            commit,
            f"Authority Lock is missing Manifest path: {missing[0]}",
            identities,
            lock,
            ref,
            repository_identity,
        )
    extra = sorted(lock_set - expected_set)
    if extra:
        return _failed(
            root,
            commit,
            f"Authority Lock has extra path not in Manifest: {extra[0]}",
            identities,
            lock,
            ref,
            repository_identity,
        )

    for rel in expected_paths:
        if rel == MANIFEST_PATH:
            continue
        _, error = _verify_locked_path(root, commit, rel, files[rel], identities)
        if error:
            return _failed(
                root,
                commit,
                error,
                identities,
                lock,
                ref,
                repository_identity,
            )

    scripts = root / "scripts"
    if str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))
    wrapper = scripts / "architect_quality_runtime.py"
    spec = importlib.util.spec_from_file_location("ev4_official_runtime", wrapper)
    if not spec or not spec.loader:
        return _failed(
            root,
            commit,
            "Official Runtime wrapper cannot be loaded.",
            identities,
            lock,
            ref,
            repository_identity,
        )
    try:
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        required = (
            "RunContext",
            "ProjectGateFinalizationResult",
            "evaluate_run",
            "evaluate_stage",
            "finalize_project_gate",
        )
        if any(getattr(module, name, None) is None for name in required):
            return _failed(
                root,
                commit,
                "Official Runtime finalization entry points are missing.",
                identities,
                lock,
                ref,
                repository_identity,
            )
        if any(
            not callable(getattr(module, name))
            for name in ("evaluate_run", "evaluate_stage", "finalize_project_gate")
        ):
            return _failed(
                root,
                commit,
                "Official Runtime finalization entry points are missing.",
                identities,
                lock,
                ref,
                repository_identity,
            )
        observed_interface = getattr(module, "RUNTIME_INTERFACE_ID", None)
        if observed_interface != lock.get("runtime_interface_id"):
            return _failed(
                root,
                commit,
                "Official Runtime interface identity mismatch.",
                identities,
                lock,
                ref,
                repository_identity,
            )
        module.load_authority(root)
    except Exception as exc:
        return _failed(
            root,
            commit,
            f"Official authority compatibility failed: {type(exc).__name__}: {exc}",
            identities,
            lock,
            ref,
            repository_identity,
        )
    return Connection(
        True,
        root,
        commit,
        "Compatible Architect Runtime authority-file identity closure.",
        dict(sorted(identities.items())),
        module,
        reference_commit,
        lock["compatibility_mode"],
        ref,
        repository_identity,
        lock["runtime_interface_id"],
    )
