"""Exact-commit Git bundle and immutable validation snapshot helpers."""
from __future__ import annotations

import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path

from .json_io import raw_sha256, write_json


@dataclass(frozen=True)
class SealedSource:
    commit: str
    bundle: Path
    sha256: str
    snapshot: Path


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(root), *args], text=True, capture_output=True)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or "Git operation failed")
    return result.stdout.strip()


def seal(source: Path, commit: str, evidence: Path) -> SealedSource:
    evidence.mkdir(parents=True, exist_ok=True)
    bundle = evidence / "sealed-source.bundle"
    ref = f"refs/ev4-stage-qc-seal/{uuid.uuid4().hex}"
    _git(source, "update-ref", ref, commit)
    try:
        result = subprocess.run(["git", "-C", str(source), "bundle", "create", str(bundle), ref], text=True, capture_output=True)
    finally:
        _git(source, "update-ref", "-d", ref)
    if result.returncode or not bundle.is_file():
        raise RuntimeError(result.stderr.strip() or "Unable to create sealed Git bundle")
    if _git(source, "bundle", "verify", str(bundle)) is None:
        raise RuntimeError("Sealed Git bundle verification failed")
    sha = raw_sha256(bundle)
    (evidence / "sealed-source.sha256").write_text(f"{sha}  {bundle.name}\n", encoding="ascii")
    snapshot = evidence / "validation-snapshot"
    result = subprocess.run(["git", "clone", "--no-checkout", str(bundle), str(snapshot)], text=True, capture_output=True)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or "Unable to create validation snapshot")
    _git(snapshot, "fetch", str(bundle), ref)
    _git(snapshot, "checkout", "--detach", "FETCH_HEAD")
    _git(snapshot, "remote", "set-url", "origin", "https://github.com/rezahh107/EV4-Architect-Repo.git")
    if _git(snapshot, "rev-parse", "HEAD") != commit:
        raise RuntimeError("Validation snapshot HEAD differs from bound Architect commit")
    write_json(evidence / "validation-snapshot-identity.json", {"bound_architect_commit": commit, "bundle_sha256": sha, "snapshot_head": commit})
    return SealedSource(commit, bundle, sha, snapshot)
