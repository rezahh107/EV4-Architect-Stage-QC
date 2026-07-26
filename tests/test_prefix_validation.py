from __future__ import annotations

import copy
import json
import os
from pathlib import Path

import pytest

import ev4_architect_stage_qc.core as core
from ev4_architect_stage_qc.architect_adapter import verify
from ev4_architect_stage_qc.core import (
    EVIDENCE_BOUNDARY,
    PREFIX_RESULT_FIELDS,
    _discover_prefix,
    run_prefinal_validation,
    run_prefix_validation,
)


def authority_root() -> Path:
    value = os.environ.get("EV4_ARCHITECT_REPO")
    if not value:
        pytest.skip("integration-only: set EV4_ARCHITECT_REPO to the locked checkout")
    return Path(value).resolve()


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value) -> None:
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def architect_outputs(root: Path) -> tuple[list[dict], dict]:
    outputs = [
        read_json(path)
        for path in sorted(
            (root / "fixtures/conversational-run/valid/minimal-complete-run").glob(
                "*.json"
            )
        )
    ]
    terminal = read_json(
        root
        / "fixtures/conversational-run/valid/terminal/project-gate-export.json"
    )
    assert len(outputs) == 11
    return outputs, terminal


def stage_folder(
    base: Path,
    outputs: list[dict],
    *,
    names: list[str] | None = None,
) -> Path:
    folder = base / "stages"
    folder.mkdir(parents=True)
    names = names or [f"{index:02d}.json" for index in range(1, len(outputs) + 1)]
    for name, value in zip(names, outputs, strict=True):
        write_json(folder / name, value)
    return folder


def generated(result, name: str):
    return read_json(result.attempt_path / "generated-artifacts" / name)


def generated_names(result) -> set[str]:
    return {
        path.name
        for path in (result.attempt_path / "generated-artifacts").iterdir()
        if path.is_file()
    }


@pytest.mark.parametrize("length", range(1, 12))
def test_official_runtime_accepts_every_nonempty_contiguous_prefix(
    tmp_path: Path,
    length: int,
):
    root = authority_root()
    outputs, _ = architect_outputs(root)
    folder = stage_folder(tmp_path, outputs[:length])

    result = run_prefix_validation(folder, root, source_kind="fixture")

    assert result.success
    assert result.code == "PREFIX_VALID"
    context = generated(result, "architect-next-stage-context.json")
    receipt = generated(result, "architect-prefix-qc-receipt.json")
    stage_results = generated(result, "architect-prefix-stage-results.json")
    run_state = generated(result, "architect-prefix-run-state.json")
    assert context["validated_stage_ids"] == [
        item["stage_id"] for item in outputs[:length]
    ]
    assert stage_results[-1]["stage_status"] == "pass"
    assert context["next_stage"] == stage_results[-1]["next_stage"]
    assert context["current_stage"] == context["next_stage"]
    assert run_state["completed_stages"] == context["validated_stage_ids"]
    assert receipt["outcome"] == {"code": "PREFIX_VALID", "success": True}
    assert receipt["evidence_boundary"] == EVIDENCE_BOUNDARY
    assert receipt["authority"]["runtime_module_origins"]


def test_prefix_context_uses_manifest_schema_and_runtime_authority_only(
    tmp_path: Path,
):
    root = authority_root()
    outputs, _ = architect_outputs(root)
    folder = stage_folder(tmp_path, outputs[:1])

    result = run_prefix_validation(folder, root, source_kind="fixture")

    context = generated(result, "architect-next-stage-context.json")
    assert context["next_stage"] == "/research"
    assert context["next_stage_version"] == "1.0.0"
    assert context["next_stage_required_quality_checks"] == [
        "research_scope_resolved",
        "platform_project_boundary_preserved",
        "unsupported_claims_remain_unknown",
    ]
    assert context["next_stage_schema"]["schema_id"] == (
        "ev4-architect-conversational-stage-output-base@1.0.0"
    )
    assert "stage_status" in context["next_stage_schema"][
        "forbidden_top_level_fields"
    ]
    assert "Unknown introductions or resolutions" in context["instruction"]
    assert "producer provenance claim" in context["instruction"]
    assert "never generate provenance" not in context["instruction"].casefold()
    assert context["evidence_boundary"] == EVIDENCE_BOUNDARY


def test_arbitrary_filenames_are_manifest_ordered_and_sources_are_immutable(
    tmp_path: Path,
):
    root = authority_root()
    outputs, _ = architect_outputs(root)
    folder = stage_folder(
        tmp_path,
        outputs[:2],
        names=["z-intake.json", "a-research.json"],
    )
    before = {path.name: path.read_bytes() for path in folder.glob("*.json")}

    result = run_prefix_validation(folder, root, source_kind="fixture")

    assert result.code == "PREFIX_VALID"
    context = generated(result, "architect-next-stage-context.json")
    assert context["validated_stage_ids"] == ["/intake", "/research"]
    assert {path.name: path.read_bytes() for path in folder.glob("*.json")} == before
    snapshots = result.attempt_path / "input-snapshot"
    for name, source_bytes in before.items():
        assert (snapshots / name).read_bytes() == source_bytes
    assert [item["stage_id"] for item in context["input_file_identities"]] == [
        "/intake",
        "/research",
    ]


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("missing-first", "exact leading Manifest prefix"),
        ("missing-middle", "exact leading Manifest prefix"),
        ("duplicate", "duplicate Stage Output"),
        ("unknown", "unknown Stage Output"),
        ("terminal", "terminal Stage Output"),
        ("wrong-version", "stage version does not match manifest"),
        ("mixed-run", "do not share one run_id"),
    ],
)
def test_invalid_prefix_structure_is_rejected_deterministically(
    tmp_path: Path,
    mutation: str,
    message: str,
):
    root = authority_root()
    outputs, terminal = architect_outputs(root)
    values = copy.deepcopy(outputs[:3])
    names = ["01.json", "02.json", "03.json"]
    if mutation == "missing-first":
        values = values[1:]
        names = names[1:]
    elif mutation == "missing-middle":
        values = [values[0], values[2]]
        names = [names[0], names[2]]
    elif mutation == "duplicate":
        values = [values[0], copy.deepcopy(values[0])]
        names = ["one.json", "two.json"]
    elif mutation == "unknown":
        values[0]["stage_id"] = "/unknown"
        values = values[:1]
        names = names[:1]
    elif mutation == "terminal":
        values = [terminal]
        names = ["terminal.json"]
    elif mutation == "wrong-version":
        values[1]["stage_version"] = "9.9.9"
    elif mutation == "mixed-run":
        values[1]["run_id"] = "another-run"
    else:
        raise AssertionError(mutation)
    folder = stage_folder(tmp_path, values, names=names)

    result = run_prefix_validation(folder, root, source_kind="fixture")

    assert not result.success
    assert result.code == "PREFIX_INPUT_INVALID"
    assert message in result.reason
    assert generated_names(result) == set()


def test_empty_and_malformed_prefixes_are_rejected(tmp_path: Path):
    root = authority_root()
    empty = tmp_path / "empty"
    empty.mkdir()
    empty_result = run_prefix_validation(empty, root, source_kind="fixture")
    assert empty_result.code == "PREFIX_INPUT_INVALID"
    assert "empty" in empty_result.reason

    malformed = tmp_path / "malformed"
    malformed.mkdir()
    (malformed / "bad.json").write_text('{"stage_id":', encoding="utf-8")
    malformed_result = run_prefix_validation(
        malformed, root, source_kind="fixture"
    )
    assert malformed_result.code == "PREFIX_INPUT_INVALID"
    assert generated_names(malformed_result) == set()

    nonobject = tmp_path / "nonobject"
    nonobject.mkdir()
    (nonobject / "bad.json").write_text("[]", encoding="utf-8")
    nonobject_result = run_prefix_validation(
        nonobject, root, source_kind="fixture"
    )
    assert nonobject_result.code == "PREFIX_INPUT_INVALID"
    assert "top-level Stage Output must be an object" in nonobject_result.reason


@pytest.mark.parametrize(
    ("expected_code", "kind", "context_name"),
    [
        (
            "PREFIX_NEEDS_INPUT",
            "user_input",
            "architect-prefix-input-context.json",
        ),
        (
            "PREFIX_BLOCKED",
            "quality",
            "architect-prefix-repair-context.json",
        ),
    ],
)
def test_official_unsuccessful_stage_is_classified_without_continuation(
    tmp_path: Path,
    expected_code: str,
    kind: str,
    context_name: str,
):
    root = authority_root()
    outputs, _ = architect_outputs(root)
    values = copy.deepcopy(outputs[:3])
    issue = {
        "issue_id": f"TEST_{expected_code}",
        "reason": "Bounded official Runtime issue.",
        "repair_stage": values[-1]["stage_id"],
        "kind": kind,
    }
    values[-1]["blockers"] = [issue]
    folder = stage_folder(tmp_path, values)

    result = run_prefix_validation(folder, root, source_kind="fixture")

    assert not result.success
    assert result.code == expected_code
    names = generated_names(result)
    assert context_name in names
    assert "architect-next-stage-context.json" not in names
    other = (
        "architect-prefix-repair-context.json"
        if expected_code == "PREFIX_NEEDS_INPUT"
        else "architect-prefix-input-context.json"
    )
    assert other not in names
    context = generated(result, context_name)
    assert context["official_blocking_issues"] == [
        {
            "issue_id": issue["issue_id"],
            "reason": issue["reason"],
            "repair_stage": issue["repair_stage"],
        }
    ]
    assert context["affected_stage"] == "/decompose"
    assert context["evaluator_returned_prior_preserved_run_state"][
        "completed_stages"
    ] == ["/intake", "/research"]
    assert "no continuation is authorized" in context["instruction"]
    assert context["evidence_boundary"] == EVIDENCE_BOUNDARY


def test_caller_authority_rejection_is_unclassified_and_issues_no_context(
    tmp_path: Path,
):
    root = authority_root()
    outputs, _ = architect_outputs(root)
    values = copy.deepcopy(outputs[:1])
    values[0]["stage_status"] = "pass"
    folder = stage_folder(tmp_path, values)

    result = run_prefix_validation(folder, root, source_kind="fixture")

    assert not result.success
    assert result.code == "PREFIX_EVALUATION_UNCLASSIFIED"
    assert "RUNTIME_CALLER_AUTHORITY_FIELD_FORBIDDEN" in result.reason
    assert generated_names(result) == set()


@pytest.mark.parametrize("failure", ["missing-run-state", "runtime-exception"])
def test_incomplete_or_exceptional_runtime_failure_issues_diagnostics_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
):
    root = authority_root()
    outputs, _ = architect_outputs(root)
    folder = stage_folder(tmp_path, outputs[:1])
    connection = verify(root)
    assert connection.ok, connection.reason
    original = connection.runtime.evaluate_run

    if failure == "missing-run-state":
        def broken(*args, **kwargs):
            result = original(*args, **kwargs)
            assert set(result) == PREFIX_RESULT_FIELDS
            result["run_state"] = None
            return result
    else:
        def broken(*args, **kwargs):
            raise RuntimeError("controlled Runtime exception")

    monkeypatch.setattr(connection.runtime, "evaluate_run", broken)
    monkeypatch.setattr(core, "verify", lambda selected: connection)

    result = run_prefix_validation(folder, root, source_kind="fixture")

    assert result.code == "PREFIX_EVALUATION_UNCLASSIFIED"
    assert generated_names(result) == set()
    assert (result.attempt_path / "diagnostics.json").is_file()


def test_semantic_provenance_limit_is_disclosed_without_new_rejection(
    tmp_path: Path,
):
    root = authority_root()
    outputs, _ = architect_outputs(root)
    folder = stage_folder(tmp_path, outputs[:1])

    result = run_prefix_validation(folder, root, source_kind="fixture")

    assert result.code == "PREFIX_VALID"
    receipt = generated(result, "architect-prefix-qc-receipt.json")
    context = generated(result, "architect-next-stage-context.json")
    for artifact in (receipt, context):
        assert artifact["evidence_boundary"] == {
            "official_runtime_execution_verified": True,
            "evaluator_derivation_verified": True,
            "semantic_provenance_truth_verified": False,
            "trusted_evidence_content_binding_verified": False,
        }


def test_handoff_prefix_reuses_prefinal_terminal_instruction_byte_for_byte(
    tmp_path: Path,
):
    root = authority_root()
    outputs, _ = architect_outputs(root)
    folder = stage_folder(tmp_path, outputs)

    prefix = run_prefix_validation(folder, root, source_kind="fixture")
    prefinal = run_prefinal_validation(folder, root, source_kind="fixture")

    assert prefix.code == "PREFIX_VALID"
    assert prefinal.code == "PREFINAL_VALID"
    prefix_instruction = generated(
        prefix, "architect-next-stage-context.json"
    )["instruction"]
    prefinal_instruction = generated(
        prefinal, "architect-final-stage-context.json"
    )["instruction"]
    assert prefix_instruction.encode("utf-8") == prefinal_instruction.encode("utf-8")
    for forbidden in (
        "project_gate_payload",
        "producer provenance",
        "stage_results",
        "run_state",
        "handoff_allowed",
    ):
        assert forbidden in prefix_instruction
