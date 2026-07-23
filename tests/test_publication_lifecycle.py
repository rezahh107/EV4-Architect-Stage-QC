from ev4_architect_stage_qc.publication_lifecycle import PublicationState, classify


def test_commitment_is_monotonic_when_handoff_is_blocked():
    receipt = {"artifact_committed": True, "handoff_allowed": False}
    assert classify(receipt, 2) is PublicationState.COMMITTED_HANDOFF_BLOCKED


def test_accepted_receipt_is_final_published():
    receipt = {"artifact_committed": True, "handoff_allowed": True, "current_revision_accepted": True, "canonical_destination_present": True, "acceptance_blockers": []}
    assert classify(receipt, 0) is PublicationState.FINAL_PUBLISHED
