"""Monotonic projection of official exporter evidence into QC lifecycle states."""
from __future__ import annotations

from enum import Enum


class PublicationState(str, Enum):
    PRECOMMIT_FAILED = "PRECOMMIT_FAILED"
    COMMITTED_HANDOFF_BLOCKED = "COMMITTED_HANDOFF_BLOCKED"
    PUBLISHED_WITH_WARNINGS = "PUBLISHED_WITH_WARNINGS"
    FINAL_PUBLISHED = "FINAL_PUBLISHED"


def classify(receipt: dict | None, exit_code: int, warnings: tuple[str, ...] = ()) -> PublicationState:
    """Receipt commitment is authoritative; exit status is consistency evidence."""
    if not receipt or receipt.get("artifact_committed") is not True:
        return PublicationState.PRECOMMIT_FAILED
    if receipt.get("handoff_allowed") is not True:
        return PublicationState.COMMITTED_HANDOFF_BLOCKED
    accepted = receipt.get("current_revision_accepted") is True and receipt.get("canonical_destination_present") is True
    if not accepted or warnings or receipt.get("acceptance_blockers"):
        return PublicationState.PUBLISHED_WITH_WARNINGS
    return PublicationState.FINAL_PUBLISHED
