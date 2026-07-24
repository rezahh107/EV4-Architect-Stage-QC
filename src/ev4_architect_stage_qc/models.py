from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
@dataclass(frozen=True)
class CoreResult:
 success: bool; attempt_path: Path|None; code: str; reason: str; next_action: str; artifacts: tuple[str,...]=()

@dataclass(frozen=True)
class PublicationLocation:
 publisher_worktree: Path; publisher_branch: str; commit: str; artifact_path: Path

@dataclass(frozen=True)
class PublisherResult:
 success: bool; published: bool; copy_warning: bool; reason: str; location: PublicationLocation|None=None; receipt: dict|None=None; receipt_update: dict|None=None; artifacts: tuple[str,...]=()
