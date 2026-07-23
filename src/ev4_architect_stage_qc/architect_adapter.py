from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
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
    trusted_context: dict | None = None
    reference_commit: str | None = None
    compatibility_mode: str | None = None
    ref: str | None = None
    repository_identity: str | None = None


def sibling_checkout():
    path = Path.cwd().parent / "EV4-Architect-Repo"
    return path if path.is_dir() else None


def _git_text(root, *args):
    try:
        return subprocess.check_output(["git", "-C", str(root), *args], text=True, stderr=subprocess.DEVNULL).strip()
    except Exception:
        return None


def _git_bytes(root, *args):
    try:
        return subprocess.check_output(["git", "-C", str(root), *args], stderr=subprocess.DEVNULL)
    except Exception:
        return None


def _identity(root):
    remote = _git_text(root, "config", "--get", "remote.origin.url") or ""
    normalized = remote.removesuffix(".git").removesuffix("/").replace("git@github.com:", "https://github.com/").casefold()
    return "rezahh107/ev4-architect-repo" if normalized.endswith("/rezahh107/ev4-architect-repo") else None


def _failed(root, commit, reason, identities, lock=None, ref=None, repository_identity=None):
    lock = lock or {}
    return Connection(False, root, commit, reason, identities, reference_commit=lock.get("reference_commit_sha"), compatibility_mode=lock.get("compatibility_mode"), ref=ref, repository_identity=repository_identity)


def verify(root: Path, lock_path: Path | None = None):
    root = Path(root).expanduser().resolve()
    identities = {}
    lock_path = lock_path or LOCK_PATH
    try:
        lock = json.loads(lock_path.read_text("utf-8"))
    except Exception:
        return _failed(root, _git_text(root, "rev-parse", "HEAD"), "QC authority compatibility lock is unavailable.", identities)
    commit = _git_text(root, "rev-parse", "HEAD")
    ref = _git_text(root, "symbolic-ref", "--short", "HEAD") or "verified-authority-files"
    if not commit:
        return _failed(root, None, "Selected Architect folder is not a Git checkout.", identities, lock, ref)
    if lock.get("compatibility_mode") != "authority_file_identity":
        return _failed(root, commit, "QC authority compatibility lock uses an unsupported compatibility mode.", identities, lock, ref)
    repository_identity = _identity(root)
    if repository_identity != lock.get("repository", "").casefold():
        return _failed(root, commit, "Selected checkout identity is not rezahh107/EV4-Architect-Repo.", identities, lock, ref)
    for rel, want in lock.get("files", {}).items():
        path = root / rel
        if not path.is_file():
            return _failed(root, commit, f"Required authority working-tree file missing: {rel}", identities, lock, ref)
        committed_oid = _git_text(root, "rev-parse", f"{commit}:{rel}")
        if not committed_oid:
            return _failed(root, commit, f"Required authority file missing from current commit: {rel}", identities, lock, ref)
        blob = _git_bytes(root, "cat-file", "blob", committed_oid)
        if blob is None:
            return _failed(root, commit, f"Locked authority blob cannot be read: {rel}", identities, lock, ref)
        got = hashlib.sha256(blob).hexdigest()
        identities[rel] = got
        if got != want:
            return _failed(root, commit, f"Authority lock mismatch for committed blob: {rel}", identities, lock, ref)
        if path.read_bytes() != blob:
            return _failed(root, commit, f"Changed authority file: {rel}", identities, lock, ref)
    spec = importlib.util.spec_from_file_location("ev4_official_runtime", root / "scripts/architect_quality_runtime.py")
    if not spec or not spec.loader:
        return _failed(root, commit, "Official evaluator cannot be loaded.", identities, lock, ref)
    try:
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        if not callable(getattr(module, "evaluate_run", None)) or not callable(getattr(module, "evaluate_stage", None)):
            return _failed(root, commit, "Official evaluator entry points are missing.", identities, lock, ref)
        module.load_authority(root)
    except Exception as exc:
        return _failed(root, commit, f"Official authority compatibility failed: {type(exc).__name__}: {exc}", identities, lock, ref)
    trusted_context = {"producer_provenance": {"repository": lock["repository"], "ref": ref, "commit_sha": commit}}
    return Connection(True, root, commit, "Compatible Architect runtime.", identities, module, trusted_context, lock["reference_commit_sha"], lock["compatibility_mode"], ref, repository_identity)
