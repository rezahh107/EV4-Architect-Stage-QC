from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ExecutionIdentity:
    wrapper_origin: Path | None
    package_origin: Path | None
    history_origin: Path | None
    finalization_origin: Path | None


@dataclass(frozen=True)
class ConnectionResult:
    ok: bool
    path: Path
    commit: str | None
    reason: str
    reference_commit: str | None = None
    runtime_interface_id: str | None = None
    compatibility_mode: str | None = None
    authority_file_count: int = 0
    ref: str | None = None
    child_pid: int | None = None
    execution_identity: ExecutionIdentity | None = None


@dataclass(frozen=True)
class CoreResult:
    success: bool
    attempt_path: Path | None
    code: str
    reason: str
    next_action: str
    artifacts: tuple[str, ...] = ()
    child_pid: int | None = None
    execution_identity: ExecutionIdentity | None = None
