from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import pytest


STATUS_PATH = Path(__file__).resolve().parents[1] / "STATUS.md"
CANONICAL_EVIDENCE_ID = "EVIDENCE-STAGEQC-PR11-FINAL-HEAD-VALIDATE"
DESCRIPTION_HEADING = "Merged Lightweight Operational Status Presentation"
CANONICAL_HEADING = "Merged lightweight status PR #11 — canonical immutable evidence"
PR10_HEADING = "Merged final Lock reconciliation PR #10 — immutable evidence"

EXACT_WORKFLOW_FIELDS = {
    "workflow",
    "workflow_run_id",
    "workflow_run_number",
    "run_number",
    "workflow_conclusion",
    "focused_cross_repository_suite",
    "full_stage_qc_suite",
    "authority_verification_artifact_digest",
    "cross_repository_artifact_digest",
    "full_tests_artifact_digest",
}
CANONICAL_REQUIRED_FIELDS = {
    "evidence_id",
    "pull_request",
    "final_pr_head",
    "merge_commit",
    "workflow",
    "workflow_run_id",
    "run_number",
    "workflow_conclusion",
    "architect_reference_head",
    "focused_cross_repository_suite",
    "full_stage_qc_suite",
    "authority_verification_artifact_digest",
    "cross_repository_artifact_digest",
    "full_tests_artifact_digest",
    "evidence_class",
}


@dataclass(frozen=True)
class MarkdownSection:
    level: int
    title: str
    body: str


_HEADING = re.compile(r"^(#{2,3})\s+(.+?)\s*$", re.MULTILINE)
_FIELD = re.compile(r"^\s*([a-z0-9_]+):", re.MULTILINE)
_EVIDENCE_DEFINITION = re.compile(r"^\s*evidence_id:\s*(\S+)\s*$", re.MULTILINE)
_EVIDENCE_REFERENCE = re.compile(
    r"^\s*[a-z0-9_]+_evidence_ref:\s*(\S+)\s*$",
    re.MULTILINE,
)
_YAML_BLOCK = re.compile(r"```yaml\s*\n(.*?)```", re.DOTALL)


def _sections(text: str) -> list[MarkdownSection]:
    headings = list(_HEADING.finditer(text))
    sections: list[MarkdownSection] = []
    for index, match in enumerate(headings):
        level = len(match.group(1))
        end = len(text)
        for later in headings[index + 1 :]:
            if len(later.group(1)) <= level:
                end = later.start()
                break
        sections.append(
            MarkdownSection(
                level=level,
                title=match.group(2),
                body=text[match.end() : end],
            )
        )
    return sections


def _section(text: str, title: str) -> MarkdownSection:
    matches = [section for section in _sections(text) if section.title == title]
    assert len(matches) == 1, f"expected exactly one section: {title}"
    return matches[0]


def _fields(section: MarkdownSection) -> set[str]:
    return set(_FIELD.findall(section.body))


def validate_status_evidence(text: str) -> None:
    definitions = _EVIDENCE_DEFINITION.findall(text)
    references = _EVIDENCE_REFERENCE.findall(text)

    assert definitions.count(CANONICAL_EVIDENCE_ID) == 1, (
        "PR #11 canonical evidence must be defined exactly once"
    )
    assert len(definitions) == len(set(definitions)), "duplicate evidence definition"
    assert references, "at least one historical evidence reference is required"
    assert set(references) <= set(definitions), "dangling historical evidence reference"

    description = _section(text, DESCRIPTION_HEADING)
    canonical = _section(text, CANONICAL_HEADING)

    assert (
        f"historical_exact_head_evidence_ref: {CANONICAL_EVIDENCE_ID}"
        in description.body
    ), "merged feature description must reference canonical PR #11 evidence"
    duplicated_fields = _fields(description) & EXACT_WORKFLOW_FIELDS
    assert not duplicated_fields, (
        "merged feature description must not duplicate exact workflow fields: "
        + ", ".join(sorted(duplicated_fields))
    )

    missing_fields = CANONICAL_REQUIRED_FIELDS - _fields(canonical)
    assert not missing_fields, (
        "canonical PR #11 evidence record is incomplete: "
        + ", ".join(sorted(missing_fields))
    )
    assert f"evidence_id: {CANONICAL_EVIDENCE_ID}" in canonical.body

    for block in _YAML_BLOCK.findall(text):
        if f"evidence_id: {CANONICAL_EVIDENCE_ID}" in block:
            continue
        if re.search(r"^\s*pull_request:\s*11\s*$", block, re.MULTILINE):
            mirrored_fields = set(_FIELD.findall(block)) & EXACT_WORKFLOW_FIELDS
            assert not mirrored_fields, (
                "parallel PR #11 exact-workflow mirror found: "
                + ", ".join(sorted(mirrored_fields))
            )


def _status_text() -> str:
    return STATUS_PATH.read_text(encoding="utf-8")


def test_pr11_final_head_evidence_has_one_definition_and_resolved_references():
    validate_status_evidence(_status_text())


def test_duplicate_canonical_definition_is_rejected():
    mutated = (
        _status_text()
        + "\n### Duplicate evidence\n\n```yaml\n"
        + f"evidence_id: {CANONICAL_EVIDENCE_ID}\n"
        + "```\n"
    )

    with pytest.raises(AssertionError, match="defined exactly once|duplicate"):
        validate_status_evidence(mutated)


def test_dangling_reference_is_rejected():
    mutated = _status_text().replace(
        f"historical_exact_head_evidence_ref: {CANONICAL_EVIDENCE_ID}",
        "historical_exact_head_evidence_ref: EVIDENCE-DOES-NOT-EXIST",
        1,
    )

    with pytest.raises(AssertionError, match="dangling"):
        validate_status_evidence(mutated)


def test_exact_run_fields_reintroduced_into_description_are_rejected():
    marker = f"historical_exact_head_evidence_ref: {CANONICAL_EVIDENCE_ID}\n"
    mutated = _status_text().replace(
        marker,
        marker + "workflow_run_id: 1\n",
        1,
    )

    with pytest.raises(AssertionError, match="must not duplicate|parallel"):
        validate_status_evidence(mutated)


def test_unrelated_pr10_historical_record_remains_a_positive_control():
    text = _status_text()
    validate_status_evidence(text)
    pr10 = _section(text, PR10_HEADING)

    assert "pull_request: 10" in pr10.body
    assert "workflow_run_id:" in pr10.body
    assert "workflow_conclusion: success" in pr10.body
