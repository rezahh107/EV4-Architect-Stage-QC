"""Monotonic projection of official exporter evidence into QC lifecycle states."""
from __future__ import annotations

from enum import Enum
from dataclasses import dataclass


class PublicationState(str, Enum):
    PRECOMMIT_FAILED = "PRECOMMIT_FAILED"
    COMMITTED_HANDOFF_BLOCKED = "COMMITTED_HANDOFF_BLOCKED"
    PUBLISHED_WITH_WARNINGS = "PUBLISHED_WITH_WARNINGS"
    FINAL_PUBLISHED = "FINAL_PUBLISHED"


@dataclass(frozen=True)
class PublicationOutcome:
    state: PublicationState
    artifact_committed: bool
    diagnostics: tuple[str, ...] = ()


def classify_publication(receipt: dict | None, receipt_update: dict | None, exit_code: int, artifact_exists: bool, warnings: tuple[str, ...] = ()) -> PublicationOutcome:
    """Classify complete official evidence without ever erasing proven commitment."""
    if not receipt or receipt.get("artifact_committed") is not True:
        return PublicationOutcome(PublicationState.PRECOMMIT_FAILED, False)
    diagnostics = list(warnings)
    committed = receipt.get("output_committed") is True
    if not committed: diagnostics.append("receipt contradicts historical artifact commitment")
    if receipt.get("handoff_allowed") is not True:
        if exit_code != 2: diagnostics.append("handoff-blocked receipt has unexpected exit code")
        return PublicationOutcome(PublicationState.COMMITTED_HANDOFF_BLOCKED, True, tuple(diagnostics))
    accepted = committed and receipt.get("current_revision_accepted") is True and receipt.get("canonical_destination_present") is True and not receipt.get("acceptance_blockers") and exit_code == 0 and artifact_exists
    update_warnings = (receipt_update or {}).get("cleanup_warnings", [])
    if not accepted or diagnostics or update_warnings or receipt.get("cleanup_warnings"):
        return PublicationOutcome(PublicationState.PUBLISHED_WITH_WARNINGS, True, tuple(diagnostics) + tuple(update_warnings))
    return PublicationOutcome(PublicationState.FINAL_PUBLISHED, True)


def classify(receipt: dict | None, exit_code: int, warnings: tuple[str, ...] = ()) -> PublicationState:
    return classify_publication(receipt, None, exit_code, True, warnings).state
