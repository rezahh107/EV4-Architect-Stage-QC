from __future__ import annotations

import copy
import importlib
import json
import os
from pathlib import Path

import pytest

import ev4_architect_stage_qc.core as core
from ev4_architect_stage_qc.architect_adapter import verify
from ev4_architect_stage_qc.core import run_final_validation, run_prefinal_validation

EXPECTED_ARCHITECT_HEAD = "5eecc46ab0bf8a48a94714558706dd3f3e7b2faf"
EXPECTED_INTERFACE = "ev4-architect-quality-runtime@2.0.0"


def authority_root() -> Path:
    value = os.environ.get("EV4_ARCHITECT_REPO")
    if not value:
        pytest.skip("integration-only: set EV4_ARCHITECT_REPO to the locked checkout")
    return Path(value)


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def architect_outputs(root: Path) -> tuple[list[dict], dict]:
    prefinal = [
        read_json(path)
        for path in sorted(
            (root / "fixtures/conversational-run/valid/minimal-complete-run").glob("*.json")
        )
    ]
    terminal = read_json(
        root / "fixtures/conversational-run/valid/terminal/project-gate-export.json"
    )
    assert len(prefinal) == 11
    return prefinal, terminal


def stage_folder(tmp_path: Path, outputs: list[dict]) -> Path:
    folder = tmp_path / "stages"
    folder.mkdir()
    for index, value in enumerate(outputs, 1):
        write_json(folder / f"{index:02d}.json", value)
    return folder


def terminal_file(tmp_path: Path, value: dict, name: str = "terminal.json") -> Path:
    path = tmp_path / name
    write_json(path, value)
    return path


def generated(result, name: str) -> dict:
    return read_json(result.attempt_path / "generated-artifacts" / name)


def test_exact_runtime_connection_and_prefinal_flow(tmp_path):
    root = authority_root()
    connection = verify(root)
    assert connection.ok, connection.reason
    assert connection.commit == EXPECTED_ARCHITECT_HEAD
    assert connection.runtime_interface_id == EXPECTED_INTERFACE
    outputs, _ = architect_outputs(root)
    folder = stage_folder(tmp_path, outputs)

    result = run_prefinal_validation(folder, root, source_kind="fixture")

    assert result.success, (result.code, result.reason, result.next_action)
    assert result.code == "PREFINAL_VALID"
    context = generated(result, "architect-final-stage-context.json")
    assert context["runtime_interface_id"] == EXPECTED_INTERFACE
    assert context["execution_context"] == {"source_kind": "fixture", "synthetic": True}
    assert "Do not generate project_gate_payload" in context["instruction"]
    assert context["run_state"]["run_id"] == outputs[0]["run_id"]
    assert context["selected_candidate_identity"] == outputs[6]["decision_input"]["selected_candidate_id"]


def test_fixture_terminal_uses_runtime_payload_and_suppresses_real_handoff(tmp_path):
    root = authority_root()
    outputs, terminal = architect_outputs(root)
    folder = stage_folder(tmp_path, outputs)
    terminal_path = terminal_file(tmp_path, terminal)

    result = run_final_validation(folder, terminal_path, root, source_kind="fixture")

    assert result.success, (result.code, result.reason, result.next_action)
    assert result.code == "FINAL_VALID"
    export = generated(result, "architect-project-gate-export.json")
    payload = generated(result, "architect-runtime-issued-payload.json")
    assert export["canonical_payload_valid"] is True
    assert export["functional_eligibility"] == {"would_allow": True, "blockers": []}
    assert export["handoff_allowed"] is False
    assert payload["synthetic"] is True
    assert payload["payload_status"] == "complete"
    assert payload["extension_records"][0]["data"]["runtime_interface_id"] == EXPECTED_INTERFACE


def test_live_terminal_can_reach_runtime_derived_handoff(tmp_path):
    root = authority_root()
    outputs, terminal = architect_outputs(root)
    folder = stage_folder(tmp_path, outputs)
    terminal_path = terminal_file(tmp_path, terminal)

    result = run_final_validation(folder, terminal_path, root, source_kind="live_conversation")

    assert result.success, (result.code, result.reason, result.next_action)
    export = generated(result, "architect-project-gate-export.json")
    assert export["runtime_issued_payload"]["synthetic"] is False
    assert export["functional_eligibility"]["would_allow"] is True
    assert export["handoff_allowed"] is True


def test_terminal_without_caller_payload_is_valid(tmp_path):
    root = authority_root()
    outputs, terminal = architect_outputs(root)
    assert "project_gate_payload" not in terminal
    folder = stage_folder(tmp_path, outputs)
    result = run_final_validation(
        folder, terminal_file(tmp_path, terminal), root, source_kind="fixture"
    )
    assert result.success, (result.code, result.reason, result.next_action)


def test_caller_authored_terminal_payload_is_rejected(tmp_path):
    root = authority_root()
    outputs, terminal = architect_outputs(root)
    terminal["project_gate_payload"] = {
        "schema_id": "ev4-architect-stage-payload@1.0.0",
        "synthetic": False,
        "fabricated": True,
    }
    folder = stage_folder(tmp_path, outputs)

    result = run_final_validation(
        folder, terminal_file(tmp_path, terminal), root, source_kind="fixture"
    )

    assert not result.success
    assert result.code == "FINAL_EVALUATION_FAILED"
    assert "project_gate_payload" in result.reason


def test_legacy_trusted_context_is_not_accepted_by_runtime_v2():
    root = authority_root()
    connection = verify(root)
    assert connection.ok, connection.reason
    outputs, _ = architect_outputs(root)
    with pytest.raises(TypeError, match="trusted_context"):
        connection.runtime.evaluate_run(
            outputs,
            root=root,
            require_terminal=False,
            run_context=connection.runtime.RunContext(source_kind="fixture"),
            trusted_context={"producer_provenance": {"fabricated": True}},
        )


def test_missing_required_class_intent_blocks_without_invented_default(tmp_path):
    root = authority_root()
    outputs, _ = architect_outputs(root)
    outputs[8] = copy.deepcopy(outputs[8])
    outputs[8]["canonical_content"].pop("class_intent")
    folder = stage_folder(tmp_path, outputs)

    result = run_prefinal_validation(folder, root, source_kind="fixture")

    assert not result.success
    assert result.code == "PREFINAL_EVALUATION_FAILED"
    assert "RUNTIME_STAGE_PREDICATE_FAILED" in result.reason
    assert "architect-section" not in result.reason


def test_blocked_consequential_stage_has_no_completion_class():
    root = authority_root()
    connection = verify(root)
    assert connection.ok, connection.reason
    outputs, _ = architect_outputs(root)
    context = connection.runtime.RunContext(source_kind="fixture")
    state = connection.runtime.initial_run_state(outputs[0]["run_id"], root=root)
    for output in outputs[:8]:
        result, state = connection.runtime.evaluate_stage(
            output["stage_id"], output, state, root=root, run_context=context
        )
        assert result["stage_status"] == "pass"
    invalid = copy.deepcopy(outputs[8])
    invalid["canonical_content"].pop("class_intent")

    result, next_state = connection.runtime.evaluate_stage(
        "/implementation", invalid, state, root=root, run_context=context
    )

    assert result["stage_status"] == "blocked"
    assert "completion_class" not in result
    assert next_state == state


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
            output["stage_id"], output, state, root=root, run_context=context
        )
        assert result["stage_status"] == "pass"
    before = copy.deepcopy(state)
    invalid = copy.deepcopy(outputs[7])
    mutation(invalid["canonical_content"])

    result, after = connection.runtime.evaluate_stage(
        "/build-tree", invalid, state, root=root, run_context=context
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
    schema = read_json(root / "schemas/ev4-architect-stage-payload.v1.schema.json")
    schema["$defs"]["payload_identity"]["properties"]["new_required_nested"] = {
        "type": "string"
    }
    schema["$defs"]["payload_identity"]["required"].append("new_required_nested")
    assembler = importlib.import_module("architect_runtime_payload_assembler")
    errors = importlib.import_module("architect_runtime_errors")
    with pytest.raises(errors.PayloadDerivationError) as caught:
        assembler.validate_derivation_schema(schema)
    assert [item.code for item in caught.value.diagnostics] == [
        "PAYLOAD_DERIVATION_REQUIRED_PATH_UNCLASSIFIED"
    ]
    assert caught.value.diagnostics[0].path == "payload_identity.new_required_nested"


def test_unexpected_runtime_programming_defect_propagates_through_core(tmp_path, monkeypatch):
    root = authority_root()
    connection = verify(root)
    assert connection.ok, connection.reason
    outputs, _ = architect_outputs(root)
    folder = stage_folder(tmp_path, outputs)

    def defect(*args, **kwargs):
        raise RuntimeError("synthetic programming defect")

    monkeypatch.setattr(connection.runtime, "evaluate_run", defect)
    monkeypatch.setattr(core, "verify", lambda selected: connection)

    with pytest.raises(RuntimeError, match="synthetic programming defect"):
        run_prefinal_validation(folder, root, source_kind="fixture")
