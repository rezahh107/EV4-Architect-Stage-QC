from types import SimpleNamespace

from ev4_architect_stage_qc.core import _context


class Runtime:
    def load_authority(self, root):
        return ({"project_execution_stages": [{"stage_version": "1.0.0"}]}, {})


def test_final_context_requests_no_caller_authority_fields():
    run = {
        "status": "valid",
        "stages_visited": ["/handoff-export"],
        "run_state": {"unknown_ledger": []},
        "results": [],
    }
    connection = SimpleNamespace(
        commit="a" * 40,
        identities={},
        runtime=Runtime(),
        path=".",
        runtime_interface_id="ev4-architect-quality-runtime@2.0.0",
        reference_commit="a" * 40,
        compatibility_mode="authority_file_identity",
        ref="fixture",
    )
    context = _context(
        connection,
        [{"stage_id": "/handoff-export"}],
        run,
        "fixture",
    )
    instruction = context["instruction"]
    assert "Generate exactly one /project-gate-export Stage Output request" in instruction
    assert "Do not generate project_gate_payload" in instruction
    assert "The Architect Runtime will assemble and validate" in instruction
    for forbidden in (
        "Runtime Context",
        "producer provenance",
        "official digests",
        "completion_class",
        "stage_status",
        "handoff_allowed",
    ):
        assert forbidden in instruction
    assert context["runtime_interface_id"] == "ev4-architect-quality-runtime@2.0.0"
    assert context["execution_context"] == {"source_kind": "fixture", "synthetic": True}
