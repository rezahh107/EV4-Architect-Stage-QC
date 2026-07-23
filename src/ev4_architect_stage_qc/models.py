from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
@dataclass(frozen=True)
class CoreResult:
 success: bool; attempt_path: Path|None; code: str; reason: str; next_action: str; artifacts: tuple[str,...]=()
