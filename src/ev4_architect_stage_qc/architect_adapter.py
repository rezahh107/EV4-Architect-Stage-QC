from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

LOCK_PATH = Path(__file__).resolve().parents[2] / "architect-authority.lock.json"


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


def sibling_checkout():
    path = Path.cwd().parent / "EV4-Architect-Repo"
    return path if path.is_dir() else None


def _git_text(root, *args):
    try:
        return subprocess.check_output(
            ["git", "-C", str(root), *args], text=True, stderr=subprocess.DEVNULL
        ).strip()
    except Exception:
        return None


def _git_bytes(root, *args):
    try:
        return subprocess.check_output(
            ["git", "-C", str(root), *args], stderr=subprocess.DEVNULL
        )
    except Exception:
        return None


def _authority_attributes(root, rel):
    values = _git_bytes(
        root, "check-attr", "-z", "filter", "working-tree-encoding", "ident", "--", rel
    )
    if values is None:
        return None
    fields = values.decode("utf-8", "surrogateescape").split("\0")
    if fields[-1] != "" or len(fields) != 10:
        return None
    return dict(zip(fields[1::3], fields[2::3], strict=True))


def _committed_blob_bytes(root: Path, oid: str) -> bytes | None:
    return _git_bytes(root, "cat-file", "blob", oid)


def _working_tree_matches_blob(root: Path, rel: str, committed_oid: str) -> bool:
    committed = _committed_blob_bytes(root, committed_oid)
    if committed is None:
        return False
    try:
        working = (root / rel).read_bytes()
    except OSError:
        return False
    return working == committed


def _identity(root):
    remote = _git_text(root, "config", "--get", "remote.origin.url") or ""
    normalized = (
        remote.removesuffix(".git")
        .removesuffix("/")
        .replace("git@github.com:", "https://github.com/")
        .casefold()
    )
    return (
        "rezahh107/ev4-architect-repo"
        if normalized.endswith("/rezahh107/ev4-architect-repo")
        else None
    )


def _failed(root, commit, reason, identities, lock=None, ref=None, repository_identity=None):
    lock = lock or {}
    return Connection(
        False,
        root,
        commit,
        reason,
        identities,
        reference_commit=lock.get("reference_commit_sha"),
        compatibility_mode=lock.get("compatibility_mode"),
        ref=ref,
        repository_identity=repository_identity,
        runtime_interface_id=lock.get("runtime_interface_id"),
    )


def verify(root: Path, lock_path: Path | None = None):
    root = Path(root).expanduser().resolve()
    identities: dict[str, str] = {}
    lock_path = lock_path or LOCK_PATH
    try:
        lock = json.loads(lock_path.read_text("utf-8"))
    except Exception:
        return _failed(
            root,
            _git_text(root, "rev-parse", "HEAD"),
            "QC authority compatibility lock is unavailable.",
            identities,
        )
    commit = _git_text(root, "rev-parse", "HEAD")
    ref = _git_text(root, "symbolic-ref", "--short", "HEAD") or "verified-authority-files"
    if not commit:
        return _failed(
            root, None, "Selected Architect folder is not a Git checkout.", identities, lock, ref
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
    repository_identity = _identity(root)
    if repository_identity != lock.get("repository", "").casefold():
        return _failed(
            root,
            commit,
            "Selected checkout identity is not rezahh107/EV4-Architect-Repo.",
            identities,
            lock,
            ref,
        )
    for rel, expected_oid in lock.get("files", {}).items():
        path = root / rel
        if not path.is_file():
            return _failed(
                root,
                commit,
                f"Required authority working-tree file missing: {rel}",
                identities,
                lock,
                ref,
                repository_identity,
            )
        attributes = _authority_attributes(root, rel)
        if attributes is None:
            return _failed(
                root,
                commit,
                f"Authority Git attributes cannot be verified: {rel}",
                identities,
                lock,
                ref,
                repository_identity,
            )
        for attribute, value in attributes.items():
            if value not in {"unspecified", "unset"}:
                return _failed(
                    root,
                    commit,
                    f"Unsafe authority Git attribute: {rel}: {attribute}={value}",
                    identities,
                    lock,
                    ref,
                    repository_identity,
                )
        committed_oid = _git_text(root, "rev-parse", f"{commit}:{rel}")
        if not committed_oid:
            return _failed(
                root,
                commit,
                f"Required authority file missing from current commit: {rel}",
                identities,
                lock,
                ref,
                repository_identity,
            )
        identities[rel] = committed_oid
        if committed_oid != expected_oid:
            return _failed(
                root,
                commit,
                f"Authority lock mismatch for committed blob: {rel}",
                identities,
                lock,
                ref,
                repository_identity,
            )
        if not _working_tree_matches_blob(root, rel, committed_oid):
            return _failed(
                root,
                commit,
                f"Authority working-tree bytes differ from committed blob: {rel}",
                identities,
                lock,
                ref,
                repository_identity,
            )

    scripts = root / "scripts"
    if str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))
    spec = importlib.util.spec_from_file_location(
        "ev4_official_runtime", scripts / "architect_quality_runtime.py"
    )
    if not spec or not spec.loader:
        return _failed(
            root, commit, "Official evaluator cannot be loaded.", identities, lock, ref
        )
    try:
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        if (
            not callable(getattr(module, "evaluate_run", None))
            or not callable(getattr(module, "evaluate_stage", None))
            or getattr(module, "RunContext", None) is None
        ):
            return _failed(
                root,
                commit,
                "Official evaluator entry points are missing.",
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
        "Compatible Architect Runtime interface v2.",
        identities,
        module,
        lock["reference_commit_sha"],
        lock["compatibility_mode"],
        ref,
        repository_identity,
        lock["runtime_interface_id"],
    )
