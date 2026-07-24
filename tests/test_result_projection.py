from pathlib import Path

import pytest

from ev4_architect_stage_qc.models import CoreResult
from ev4_architect_stage_qc.result_projection import project_result


@pytest.mark.parametrize("code, phrase", [
    ("FINAL_VALIDATION_FAILED", "validation failed"),
    ("FINAL_VALID_PUBLICATION_UNAVAILABLE", "publication is unavailable"),
    ("FINAL_PUBLICATION_PRECOMMIT_FAILED", "before commit"),
    ("FINAL_COMMITTED_HANDOFF_BLOCKED", "handoff is blocked"),
    ("FINAL_PUBLISHED_COPY_WARNING", "copy needs attention"),
    ("FINAL_PUBLISHED", "FINAL_PUBLISHED"),
    ("INTERNAL_APPLICATION_ERROR", "Internal application error"),
])
def test_each_final_state_has_distinct_projection(code, phrase):
    title, _ = project_result(CoreResult(code == "FINAL_PUBLISHED", Path("attempt"), code, "reason", "action"))
    assert phrase in title
