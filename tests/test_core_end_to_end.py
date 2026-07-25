from __future__ import annotations

import copy
import importlib
import json
import os
from pathlib import Path

import pytest

import ev4_architect_stage_qc.core as core
from ev4_architect_stage_qc.architect_adapter import LOCK_PATH, verify
from ev4_architect_stage_qc.core import run_final_validation, run_prefinal_validation

ARTIFACT = "architect-project-gate.json"
RECEIPT = "architect-project-gate-receipt.json"


def dependency_lock() -> dict:
    return json.loads(LOCK_PATH.read_text(encoding="utf-8"))


def authority_root() -> Path:
    value = os.environ.get("EV4_ARCHITECT_REPO")
    if not value:
        pytest.skip("integration-only: set EV4_ARCHITECT_REPO to the locked checkout")
    return Path(value)


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: dict) -> None:
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def architect_outputs(root: Path) -> tuple[list[dict], dict]:
    prefinal = [
        read_json(path)
        for path in sorted(
            (
                root
                / "fixtures/conversational-run/valid/minimal-complete-run"
            ).glob("*.json")
        )
    ]
    terminal = read_json(
        root
        / "fixtures/conversational-run/valid/terminal/project-gate-export.json"
    )
    assert len(prefinal) == 11
    return prefinal, terminal


def stage_folder(base: Path, outputs: list[dict]) -> Path:
    folder = base / "stages"
    folder.mkdir(parents=True)
    for index, value in enumerate(outputs, 1):
        write_json(folder / f"{index:02d}.json", value)
    return folder


def terminal_file(base: Path, value: dict, name: str = "terminal.json") -> Path:
    base.mkdir(parents=True, exist_ok=True)
    path = base / name
    write_json(path, value)
    return path


def generated(result, name: str) -> dict:
    return read_json(result.attempt_path / "generated-artifacts" / name)


def test_exact_runtime_connection_and_prefinal_flow(tmp_path: Path):
    root = authority_root()
    connection = verify(root)
    assert connection.ok, connection.reason
    lock = dependency_lock()
    assert connection.commit == lock["reference_commit_sha"]
    assert connection.runtime_interface_id == lock["runtime_interface_id"]
    outputs, _ = architect_outputs(root)
    folder = stage_folder(tmp_path, outputs)

    result = run_prefinal_validation(folder, root, source_kind="fixture")

    assert result.success
    assert result.code == "PREFINAL_VALID"
    context = generated(result, "architect-final-stage-context.json")
    assert context["runtime_interface_id"] == lock["runtime_interface_id"]
    assert context["execution_context"] == {
        "source_kind": "fixture",
        "synthetic": True,
    }
    assert "producer-gate-export.v1" in context["instruction"]
    assert "project_gate_payload" in context["instruction"]
    assert context["run_state"]["run_id"] == outputs[0]["run_id"]
    assert (
        context["selected_candidate_identity"]
        == outputs[6]["decision_input"]["selected_candidate_id"]
    )


def _terminal_form(terminal: dict, form: str) -> dict:
    value = copy.deepcopy(terminal)
    value.pop("presentation_note", None)
    if form == "exact-request":
        value["export_request"] = {"format": "producer-gate-export.v1"}
    elif form == "request-with-note":
        value["export_request"] = {
            "format": "producer-gate-export.v1",
            "presentation_note": "Publish the exact Runtime-owned export.",
        }
    elif form == "presentation-note-only":
        value.pop("export_request", None)
        value["presentation_note"] = "Publish the exact Runtime-owned export."
    else:
        raise AssertionError(form)
    return value


@pytest.mark.parametrize(
    "form",
    ["exact-request", "request-with-note", "presentation-note-only"],
)
def test_each_supported_terminal_request_reaches_runtime_and_publishes_blocked_fixture(
    tmp_path: Path,
    form: str,
):
    root = authority_root()
    outputs, terminal = architect_outputs(root)
    terminal = _terminal_form(terminal, form)
    folder = stage_folder(tmp_path / form, outputs)

    result = run_final_validation(
        folder,
        terminal_file(tmp_path / form, terminal),
        root,
        source_kind="fixture",
    )

    assert not result.success
    assert result.code == "FINAL_HANDOFF_BLOCKED"
    artifact = generated(result, ARTIFACT)
    receipt = generated(result, RECEIPT)
    assert artifact["handoff"]["allowed"] is False
    assert receipt["finalization"]["status"] == "published_blocked"
    assert receipt["synthetic"] is True


def test_live_terminal_can_reach_runtime_derived_allowed_handoff(tmp_path: Path):
    root = authority_root()
    outputs, terminal = architect_outputs(root)
    folder = stage_folder(tmp_path, outputs)

    result = run_final_validation(
        folder,
        terminal_file(tmp_path, _terminal_form(terminal, "exact-request")),
        root,
        source_kind="live_conversation",
    )

    assert result.success
    assert result.code == "FINAL_VALID"
    artifact = generated(result, ARTIFACT)
    receipt = generated(result, RECEIPT)
    assert artifact["handoff"]["allowed"] is True
    assert receipt["finalization"]["status"] == "published_allowed"
    assert receipt["synthetic"] is False


def _mutate_terminal(value: dict, mutation: str) -> None:
    if mutation == "missing-request":
        value.pop("export_request", None)
        value.pop("presentation_note", None)
    elif mutation == "empty-request":
        value["export_request"] = {}
        value.pop("presentation_note", None)
    elif mutation == "non-object-request":
        value["export_request"] = "producer-gate-export.v1"
    elif mutation == "wrong-format":
        value["export_request"] = {"format": "unsupported.v1"}
    elif mutation == "empty-nested-note":
        value["export_request"] = {
            "format": "producer-gate-export.v1",
            "presentation_note": "",
        }
    elif mutation == "whitespace-nested-note":
        value["export_request"] = {
            "format": "producer-gate-export.v1",
            "presentation_note": "   ",
        }
    elif mutation == "empty-top-note":
        value.pop("export_request", None)
        value["presentation_note"] = ""
    elif mutation == "whitespace-top-note":
        value.pop("export_request", None)
        value["presentation_note"] = "   "
    elif mutation == "canonical-content":
        value["canonical_content"] = {}
    elif mutation == "project-gate-payload":
        value["project_gate_payload"] = {"fabricated": True}
    elif mutation == "handoff-allowed":
        value["handoff_allowed"] = True
    elif mutation == "stage-results":
        value["stage_results"] = [{"stage_status": "pass"}]
    elif mutation == "run-state":
        value["run_state"] = {"completed_stages": ["/project-gate-export"]}
    else:
        raise AssertionError(mutation)


@pytest.mark.parametrize(
    "mutation",
    [
        "missing-request",
        "empty-request",
        "non-object-request",
        "wrong-format",
        "empty-nested-note",
        "whitespace-nested-note",
        "empty-top-note",
        "whitespace-top-note",
        "canonical-content",
        "project-gate-payload",
        "handoff-allowed",
        "stage-results",
        "run-state",
    ],
)
def test_invalid_terminal_mutations_fail_without_publication_or_prefinal_state_change(
    tmp_path: Path,
    mutation: str,
):
    root = authority_root()
    outputs, terminal = architect_outputs(root)
    case = tmp_path / mutation
    folder = stage_folder(case, outputs)

    prefinal = run_prefinal_validation(folder, root, source_kind="fixture")
    assert prefinal.code == "PREFINAL_VALID"
    prefinal_state = (
        prefinal.attempt_path
        / "generated-artifacts"
        / "architect-prefinal-run-state.json"
    )
    state_bytes = prefinal_state.read_bytes()
    source_bytes = {
        path.name: path.read_bytes()
        for path in folder.glob("*.json")
    }

    invalid = _terminal_form(terminal, "exact-request")
    _mutate_terminal(invalid, mutation)
    result = run_final_validation(
        folder,
        terminal_file(case, invalid),
        root,
        source_kind="fixture",
    )

    assert not result.success
    assert result.code == "FINALIZATION_FAILED"
    assert prefinal_state.read_bytes() == state_bytes
    assert {
        path.name: path.read_bytes()
        for path in folder.glob("*.json")
    } == source_bytes
    generated_dir = result.attempt_path / "generated-artifacts"
    assert not (generated_dir / ARTIFACT).exists()
    assert not (generated_dir / RECEIPT).exists()
    publication = result.attempt_path / "architect-publication"
    assert not (publication / ARTIFACT).exists()
    assert not (publication / RECEIPT).exists()
    if mutation in {
        "missing-request",
        "empty-request",
        "non-object-request",
        "wrong-format",
        "empty-nested-note",
        "whitespace-nested-note",
        "empty-top-note",
        "whitespace-top-note",
        "canonical-content",
    }:
        assert "RUNTIME_PROJECT_GATE_EXPORT_REQUEST_INVALID" in result.reason
    else:
        assert "RUNTIME_CALLER_" in result.reason


def test_runtime_artifact_and_receipt_are_copied_byte_identically(tmp_path: Path):
    root = authority_root()
    outputs, terminal = architect_outputs(root)
    folder = stage_folder(tmp_path, outputs)
    result = run_final_validation(
        folder,
        terminal_file(tmp_path, _terminal_form(terminal, "exact-request")),
        root,
        source_kind="live_conversation",
    )
    assert result.code == "FINAL_VALID"
    generated_dir = result.attempt_path / "generated-artifacts"
    publication_dir = result.attempt_path / "architect-publication"
    assert (generated_dir / ARTIFACT).read_bytes() == (
        publication_dir / ARTIFACT
    ).read_bytes()
    assert (generated_dir / RECEIPT).read_bytes() == (
        publication_dir / RECEIPT
    ).read_bytes()


def test_downstream_only_obligation_remains_successful_with_flags(tmp_path: Path):
    root = authority_root()
    outputs, terminal = architect_outputs(root)
    outputs[0]["unknown_introductions"] = [
        {
            "unknown_id": "U-responsive-stage-qc",
            "statement": "Responsive runtime evidence remains downstream-owned.",
            "downstream_critical": False,
        }
    ]
    folder = stage_folder(tmp_path, outputs)
    result = run_final_validation(
        folder,
        terminal_file(tmp_path, _terminal_form(terminal, "exact-request")),
        root,
        source_kind="live_conversation",
    )
    assert result.code == "FINAL_VALID"
    artifact = generated(result, ARTIFACT)
    assert artifact["handoff"]["allowed"] is True
    assert artifact["handoff"]["status"] == "successful_with_flags"


def test_architect_transition_blocker_remains_fail_closed(tmp_path: Path):
    root = authority_root()
    outputs, terminal = architect_outputs(root)
    outputs[0]["unknown_introductions"] = [
        {
            "unknown_id": "U-architect-stage-qc",
            "statement": "Architect acceptance evidence remains unresolved.",
            "downstream_critical": True,
        }
    ]
    folder = stage_folder(tmp_path, outputs)
    result = run_final_validation(
        folder,
        terminal_file(tmp_path, _terminal_form(terminal, "exact-request")),
        root,
        source_kind="live_conversation",
    )
    assert not result.success
    assert result.code == "FINALIZATION_FAILED"
    assert "RUNTIME_CRITICAL_UNKNOWN_ACTIVE" in result.reason
    assert not (
        result.attempt_path / "generated-artifacts" / ARTIFACT
    ).exists()


def test_missing_required_class_intent_blocks_without_invented_default(
    tmp_path: Path,
):
    root = authority_root()
    outputs, _ = architect_outputs(root)
    outputs[8]["canonical_content"].pop("class_intent")
    folder = stage_folder(tmp_path, outputs)

    result = run_prefinal_validation(folder, root, source_kind="fixture")

    assert not result.success
    assert result.code == "PREFINAL_EVALUATION_FAILED"
    assert "RUNTIME_STAGE_PREDICATE_FAILED" in result.reason
    assert "architect-section" not in result.reason


@pytest.mark.parametrize(
    "mutation",
    [
        lambda tree: tree["nodes"].append(
            {"id": "orphan", "role": "normal_flow_group", "children": []}
        ),
        lambda tree: tree["nodes"][1]["children"].append("node-wrapper"),
        lambda tree: tree["nodes"][0]["children"].append("missing-node"),
        lambda tree: tree["nodes"][2]["children"].append("node-wrapper"),
        lambda tree: tree["nodes"][2].__setitem__("role", "unclassified-role"),
    ],
    ids=["orphan", "cycle", "unknown-child", "multiple-parents", "unclassified-role"],
)
def test_malformed_build_tree_blocks_before_terminal_and_preserves_state(mutation):
    root = authority_root()
    connection = verify(root)
    assert connection.ok, connection.reason
    outputs, _ = architect_outputs(root)
    context = connection.runtime.RunContext(source_kind="fixture")
    state = connection.runtime.initial_run_state(outputs[0]["run_id"], root=root)
    for output in outputs[:7]:
        result, state = connection.runtime.evaluate_stage(
            output["stage_id"],
            output,
            state,
            root=root,
            run_context=context,
        )
        assert result["stage_status"] == "pass"
    before = copy.deepcopy(state)
    invalid = copy.deepcopy(outputs[7])
    mutation(invalid["canonical_content"])

    result, after = connection.runtime.evaluate_stage(
        "/build-tree",
        invalid,
        state,
        root=root,
        run_context=context,
    )

    assert result["stage_status"] == "blocked"
    assert "completion_class" not in result
    assert result["next_stage"] is None
    assert result["decision_state"]["build_tree_digest"] is None
    assert after == before
    assert "/build-tree" not in after["completed_stages"]
    assert "/project-gate-export" not in after["completed_stages"]


def test_derivation_schema_drift_is_rejected_by_exact_architect_runtime():
    root = authority_root()
    connection = verify(root)
    assert connection.ok, connection.reason
    schema = read_json(
        root / "schemas/ev4-architect-stage-payload.v1.schema.json"
    )
    schema["$defs"]["payload_identity"]["properties"][
        "new_required_nested"
    ] = {"type": "string"}
    schema["$defs"]["payload_identity"]["required"].append(
        "new_required_nested"
    )
    assembler = importlib.import_module("architect_runtime_payload_assembler")
    errors = importlib.import_module("architect_runtime_errors")
    with pytest.raises(errors.PayloadDerivationError) as caught:
        assembler.validate_derivation_schema(schema)
    assert [item.code for item in caught.value.diagnostics] == [
        "PAYLOAD_DERIVATION_REQUIRED_PATH_UNCLASSIFIED"
    ]


def test_unexpected_runtime_programming_defect_propagates_through_core(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    root = authority_root()
    connection = verify(root)
    assert connection.ok, connection.reason
    outputs, terminal = architect_outputs(root)
    folder = stage_folder(tmp_path, outputs)

    def defect(*args, **kwargs):
        raise RuntimeError("synthetic programming defect")

    monkeypatch.setattr(connection.runtime, "finalize_project_gate", defect)
    monkeypatch.setattr(core, "verify", lambda selected: connection)

    with pytest.raises(RuntimeError, match="synthetic programming defect"):
        run_final_validation(
            folder,
            terminal_file(
                tmp_path,
                _terminal_form(terminal, "exact-request"),
            ),
            root,
            source_kind="fixture",
        )
