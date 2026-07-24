"""The single, monotonic authority for publication lifecycle meaning."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class PublicationState(str, Enum):
    PRECOMMIT_FAILED = "PRECOMMIT_FAILED"
    COMMITTED_HANDOFF_BLOCKED = "COMMITTED_HANDOFF_BLOCKED"
    COMMITTED_EVIDENCE_CONTRADICTION = "COMMITTED_EVIDENCE_CONTRADICTION"
    PUBLISHED_WITH_WARNINGS = "PUBLISHED_WITH_WARNINGS"
    FINAL_PUBLISHED = "FINAL_PUBLISHED"


@dataclass(frozen=True)
class PublicationOutcome:
    state: PublicationState
    artifact_committed: bool
    output_committed: bool = False
    handoff_allowed_by_receipt: bool = False
    handoff_trusted_by_qc: bool = False
    current_revision_accepted: bool = False
    canonical_destination_present: bool = False
    committed_output_location: str | None = None
    verified_artifact_path: Path | None = None
    official_export_exit_code: int | None = None
    historical_receipt: dict | None = None
    receipt_update: dict | None = None
    acceptance_blockers: tuple[str, ...] = ()
    cleanup_warnings: tuple[str, ...] = ()
    receipt_update_warnings: tuple[str, ...] = ()
    wrapper_warnings: tuple[str, ...] = ()
    contradiction_diagnostics: tuple[str, ...] = ()
    publisher_branch: str | None = None
    publisher_worktree: Path | None = None
    recovery_record_path: Path | None = None
    convenience_copy_complete: bool = False

    @property
    def diagnostics(self) -> tuple[str, ...]:
        return self.contradiction_diagnostics + self.wrapper_warnings


def classify_publication(receipt: dict | None, receipt_update: dict | None, exit_code: int,
                         artifact_exists: bool, warnings: tuple[str, ...] = (),
                         *, artifact_valid: bool = True) -> PublicationOutcome:
    """Classify receipt evidence; commitment is captured before all later checks."""
    if not receipt or receipt.get("artifact_committed") is not True:
        return PublicationOutcome(PublicationState.PRECOMMIT_FAILED, False,
                                  official_export_exit_code=exit_code,
                                  historical_receipt=receipt, receipt_update=receipt_update,
                                  wrapper_warnings=warnings)
    output_committed = receipt.get("output_committed") is True
    handoff = receipt.get("handoff_allowed") is True
    accepted = receipt.get("current_revision_accepted") is True
    destination = receipt.get("canonical_destination_present") is True
    blockers = tuple(receipt.get("acceptance_blockers") or ())
    cleanup = tuple(receipt.get("cleanup_warnings") or ())
    update_warnings = tuple((receipt_update or {}).get("cleanup_warnings") or ())
    contradictions: list[str] = []
    if not output_committed: contradictions.append("artifact_committed=true but output_committed is not true")
    if not handoff and exit_code != 2: contradictions.append("blocked handoff has an exit code other than 2")
    if handoff and exit_code != 0: contradictions.append("accepted-looking receipt has an exit code other than 0")
    if handoff and not accepted: contradictions.append("handoff allowed but current revision is not accepted")
    if handoff and not destination: contradictions.append("handoff allowed but canonical destination is absent")
    if handoff and blockers: contradictions.append("handoff allowed with acceptance blockers")
    if destination and not artifact_exists: contradictions.append("canonical destination claimed but artifact is absent")
    if artifact_exists and not artifact_valid: contradictions.append("committed artifact bytes are invalid or inconsistent")
    common = dict(artifact_committed=True, output_committed=output_committed,
                  handoff_allowed_by_receipt=handoff, current_revision_accepted=accepted,
                  canonical_destination_present=destination, official_export_exit_code=exit_code,
                  historical_receipt=receipt, receipt_update=receipt_update,
                  acceptance_blockers=blockers, cleanup_warnings=cleanup,
                  receipt_update_warnings=update_warnings, wrapper_warnings=warnings)
    if contradictions:
        return PublicationOutcome(PublicationState.COMMITTED_EVIDENCE_CONTRADICTION,
                                  handoff_trusted_by_qc=False,
                                  contradiction_diagnostics=tuple(contradictions), **common)
    if not handoff:
        return PublicationOutcome(PublicationState.COMMITTED_HANDOFF_BLOCKED,
                                  handoff_trusted_by_qc=False, **common)
    trusted = output_committed and handoff and accepted and destination and not blockers and artifact_exists and artifact_valid and exit_code == 0
    if cleanup or update_warnings or warnings:
        return PublicationOutcome(PublicationState.PUBLISHED_WITH_WARNINGS,
                                  handoff_trusted_by_qc=trusted, **common)
    return PublicationOutcome(PublicationState.FINAL_PUBLISHED, handoff_trusted_by_qc=trusted, **common)


def classify(receipt: dict | None, exit_code: int, warnings: tuple[str, ...] = ()) -> PublicationState:
    return classify_publication(receipt, None, exit_code, True, warnings).state
