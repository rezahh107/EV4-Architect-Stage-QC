"""GUI-owned orchestration around the official Architect Project Gate exporter."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

from .architect_adapter import verify
from .json_io import atomic_write, canonical_bytes, load_strict, write_json
from .models import PublicationLocation, PublisherResult


def resolve_console_python() -> str:
    executable = Path(sys.executable)
    if os.name == "nt" and executable.name.casefold() == "pythonw.exe":
        return str(executable.with_name("python.exe"))
    return str(executable)


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or "Git command failed")
    return result.stdout.strip()


def _publication_root() -> Path:
    base = os.environ.get("LOCALAPPDATA") if os.name == "nt" else None
    return Path(base) / "EV4ArchitectStageQC" / "publisher-worktrees" if base else Path.home() / ".local" / "share" / "EV4ArchitectStageQC" / "publisher-worktrees"


def create_unique_publisher_worktree(source: Path, publication_root: Path | None = None) -> PublicationLocation:
    commit = _git(source, "rev-parse", "HEAD")
    root = publication_root or _publication_root(); root.mkdir(parents=True, exist_ok=True)
    for _ in range(20):
        unique = uuid.uuid4().hex[:12]
        branch = f"ev4-stage-qc-publisher-{commit[:12]}-{unique}"
        worktree = root / unique
        result = subprocess.run(["git", "-C", str(source), "worktree", "add", "-b", branch, str(worktree), commit], capture_output=True, text=True)
        if result.returncode == 0:
            if _git(worktree, "rev-parse", "HEAD") != commit:
                raise RuntimeError("Publisher HEAD does not match the validated Architect commit.")
            if not _git(worktree, "symbolic-ref", "--short", "HEAD"):
                raise RuntimeError("Publisher worktree is not on a Named Branch.")
            return PublicationLocation(worktree, branch, commit, worktree / ".ev4-stage-qc")
        if "already exists" not in result.stderr:
            raise RuntimeError(result.stderr.strip() or "Publisher worktree creation failed.")
    raise RuntimeError("Unable to create a unique Publisher branch and worktree.")


def _parse_object(text: str, label: str) -> dict:
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Official {label} is not valid JSON.") from exc
    if not isinstance(value, dict):
        raise ValueError(f"Official {label} must be a JSON object.")
    return value


def publish(payload: dict, run_id: str, architect_root: Path, attempt: Path, publication_root: Path | None = None) -> PublisherResult:
    """Publish only an already Runtime-validated payload through the official CLI."""
    source = verify(architect_root)
    if not source.ok:
        return PublisherResult(False, False, False, source.reason)
    location: PublicationLocation | None = None
    published = False
    try:
        location = create_unique_publisher_worktree(source.path, publication_root)
        publisher = verify(location.publisher_worktree)
        if not publisher.ok:
            raise RuntimeError(f"Publisher Authority mismatch: {publisher.reason}")
        if publisher.commit != source.commit:
            raise RuntimeError("Publisher HEAD does not match the validated Architect commit.")
        publication_id = location.publisher_worktree.name
        rel_dir = Path(".ev4-stage-qc") / "publications" / publication_id
        payload_path = location.publisher_worktree / rel_dir / "validated-architect-stage-payload.json"
        output_path = location.publisher_worktree / rel_dir / "architect-project-gate.json"
        atomic_write(payload_path, canonical_bytes(payload))
        command = [resolve_console_python(), "scripts/export-architect-project-gate.py", "--repo-root", str(location.publisher_worktree), "--payload", str(rel_dir / payload_path.name), "--run-id", run_id, "--output", str(rel_dir / output_path.name), "--format", "json"]
        kwargs = {"cwd": location.publisher_worktree, "capture_output": True, "text": True}
        if os.name == "nt": kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        process = subprocess.run(command, **kwargs)
        diagnostics = attempt / "diagnostics"
        if process.returncode:
            diagnostics.mkdir(parents=True, exist_ok=True)
            atomic_write(diagnostics / "official-export-stdout.txt", process.stdout.encode())
            atomic_write(diagnostics / "official-export-stderr.txt", process.stderr.encode())
            raise RuntimeError(f"Official exporter failed with exit code {process.returncode}.")
        receipt = _parse_object(process.stdout, "historical receipt")
        if not output_path.is_file():
            raise RuntimeError("Official exporter exited successfully but did not create the artifact.")
        load_strict(output_path)
        required = ("artifact_committed", "output_committed", "handoff_allowed", "current_revision_accepted", "canonical_destination_present")
        if not all(receipt.get(key) is True for key in required) or receipt.get("acceptance_blockers"):
            raise RuntimeError("Official receipt did not establish publication acceptance.")
        published = True
        generated = attempt / "generated-artifacts"
        atomic_write(generated / "validated-architect-stage-payload.json", canonical_bytes(payload))
        shutil.copyfile(output_path, generated / "architect-project-gate.json")
        write_json(generated / "architect-project-gate-receipt.json", receipt)
        if process.stderr.strip():
            update = _parse_object(process.stderr, "receipt update")
            write_json(generated / "architect-project-gate-receipt-update.json", update)
        if (generated / "architect-project-gate.json").read_bytes() != output_path.read_bytes():
            raise RuntimeError("Attempt artifact copy does not match official artifact bytes.")
        location = PublicationLocation(location.publisher_worktree, location.publisher_branch, location.commit, output_path)
        return PublisherResult(True, True, False, "Official Project Gate publication completed.", location, receipt, tuple(p.name for p in generated.iterdir()))
    except Exception as exc:
        # A worktree containing an official artifact is evidence and must outlive the attempt.
        return PublisherResult(False, published, published, str(exc), location)
