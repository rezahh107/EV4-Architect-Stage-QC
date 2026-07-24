"""Single user-facing projection for final validation/publication outcomes."""
from __future__ import annotations

from .models import CoreResult

_MESSAGES = {
    "FINAL_VALIDATION_FAILED": ("✕ Final validation failed", False),
    "FINAL_VALID_PUBLICATION_UNAVAILABLE": ("✓ Final validation passed\n⚠ Official publication is unavailable", True),
    "FINAL_PUBLICATION_PRECOMMIT_FAILED": ("✓ Final validation passed\n✕ Official publication failed before commit", False),
    "FINAL_COMMITTED_HANDOFF_BLOCKED": ("⚠ Official artifact was committed, but handoff is blocked", True),
    "FINAL_PUBLISHED_COPY_WARNING": ("⚠ Official publication completed; Attempt-folder copy needs attention", True),
    "FINAL_PUBLISHED_WITH_WARNINGS": ("⚠ Official publication completed with warnings", True),
    "FINAL_PUBLISHED": ("✓ Final validation and official publication completed\nStatus: FINAL_PUBLISHED", True),
    "INTERNAL_APPLICATION_ERROR": ("✕ Internal application error", False),
}


def project_result(result: CoreResult) -> tuple[str, bool]:
    """Return status text and whether the Attempt folder is useful to open."""
    return _MESSAGES.get(result.code, ("✓ Validation completed successfully" if result.success else "✕ Validation failed", bool(result.attempt_path)))
