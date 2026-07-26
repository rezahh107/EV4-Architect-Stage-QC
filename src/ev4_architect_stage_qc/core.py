from __future__ import annotations

import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from .architect_adapter import verify
from .json_io import (
    atomic_write,
    canonical_sha256,
    load_strict,
    raw_sha256,
    write_json,
)
from .models import CoreResult

APP_VERSION = "0.3.0"
PREFIX_CONTEXT_VERSION = "1.1.0"
PREFIX_RESULT_FIELDS = {
    "status",
    "errors",
    "diagnostics",
    "results",
    "stages_visited",
    "all_required_stages_visited",
    "terminal_stage",
    "run_state",
}
RUN_STATE_FIELDS = {
    "run_id",
    "current_stage",
    "completed_stages",
    "unknown_ledger",
    "selected_candidate_id",
    "selected_candidate_locked",
    "build_tree_digest",
    "implementation_digest",
    "evaluated_stage_outputs",
    "derived_stage_results",
}
STAGE_OUTPUT_SCHEMA_PATH = (
    "schemas/architect-conversational-stage-output-base.v1.schema.json"
)
EVIDENCE_BOUNDARY = {
    "official_runtime_execution_verified": True,
    "evaluator_derivation_verified": True,
    "semantic_provenance_truth_verified": False,
    "trusted_evidence_content_binding_verified": False,
}
TERMINAL_INSTRUCTION = (
    "Generate exactly one /project-gate-export Stage Output request for this Run. "
    "Use exactly one supported non-authorizing request form: either export_request "
    "with format producer-gate-export.v1 and an optional non-empty presentation_note, "
    "or a non-empty top-level presentation_note with no export_request. Preserve the "
    "supplied run identity and selected Candidate. Do not generate project_gate_payload, "
    "Runtime Context, producer provenance, official digests, completion_class, stage_status, "
    "next_stage, canonical_payload_valid, legacy_export_substituted, functional eligibility, "
    "handoff_allowed, payload, payload_path, stage_results, run_state, provenance, receipt, "
    "artifact, capability, or runtime_capability. The Architect Runtime will assemble, "
    "validate, finalize, and publish the canonical output."
)


def _utc():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _attempt(folder: Path):
    base = folder / "results"
    base.mkdir(parents=True, exist_ok=True)
    for number in range(1, 1_000_000):
        path = base / f"attempt-{number:04d}"
        try:
            path.mkdir()
            (path / "input-snapshot").mkdir()
            (path / "generated-artifacts").mkdir()
            return path
        except FileExistsError:
            continue
    raise RuntimeError("No attempt directory available")


def _record(attempt, code, reason, action, success=False, artifacts=()):
    write_json(attempt / "diagnostics.json", {"code": code, "reason": reason, "next_action": action})
    atomic_write(
        attempt / "execution-summary.txt",
        f"{code}: {reason}\nNext action: {action}\n".encode(),
    )
    write_json(
        attempt / "attempt-metadata.json",
        {
            "schema_version": "1.0",
            "application_version": APP_VERSION,
            "attempt_id": attempt.name,
            "started_at_utc": _utc(),
            "attempt_path": str(attempt),
        },
    )
    return CoreResult(success, attempt, code, reason, action, tuple(artifacts))


def _diagnostic_text(item: dict) -> str:
    """Preserve Runtime diagnostic identity while retaining its human message."""
    code = item.get("code")
    message = item.get("message")
    if code and message:
        return f"{code}: {message}"
    return str(code or message or "Architect Runtime finalization failed.")


def _discover(folder, manifest, excluded: set[Path] | None = None):
    excluded = {item.resolve() for item in (excluded or set())}
    files = sorted(
        (
            path
            for path in folder.iterdir()
            if path.is_file()
            and path.suffix.casefold() == ".json"
            and path.resolve() not in excluded
        ),
        key=lambda path: path.name.casefold(),
    )
    outputs = []
    seen = {}
    for path in files:
        value = load_strict(path)
        for key in ("stage_id", "stage_version", "run_id"):
            if not value.get(key):
                raise ValueError(f"{path.name}: missing required identity field {key}")
        stage = value["stage_id"]
        if stage in seen:
            raise ValueError(
                f"duplicate Stage Output for {stage}: {seen[stage].name}, {path.name}"
            )
        seen[stage] = path
        outputs.append((path, value))
    stages = manifest["project_execution_stages"]
    prefinal = stages[:-1]
    expected = [item["stage_id"] for item in prefinal]
    extras = sorted(set(seen) - set(expected))
    if extras:
        raise ValueError(f"unexpected Stage Output(s): {', '.join(extras)}")
    missing = [item for item in expected if item not in seen]
    if missing:
        raise ValueError(f"missing mandatory Stage Output(s): {', '.join(missing)}")
    run_ids = {value["run_id"] for _, value in outputs}
    if len(run_ids) != 1:
        raise ValueError("Stage Outputs do not share one run_id")
    ordered = []
    by_stage = {value["stage_id"]: (path, value) for path, value in outputs}
    for stage in prefinal:
        path, value = by_stage[stage["stage_id"]]
        if value["stage_version"] != stage["stage_version"]:
            raise ValueError(f"{path.name}: stage version does not match manifest")
        ordered.append((path, value))
    return ordered


def _manifest_prefix_authority(manifest: dict[str, Any]):
    stages = manifest.get("project_execution_stages")
    terminal_id = manifest.get("final_project_gate_export_stage")
    if (
        not isinstance(stages, list)
        or len(stages) < 2
        or not isinstance(terminal_id, str)
    ):
        raise ValueError("Architect Pipeline Manifest is malformed")
    matches = [
        index
        for index, item in enumerate(stages)
        if isinstance(item, dict) and item.get("stage_id") == terminal_id
    ]
    if matches != [len(stages) - 1]:
        raise ValueError("Architect terminal Stage identity/order is malformed")
    for item in stages:
        if (
            not isinstance(item, dict)
            or not isinstance(item.get("stage_id"), str)
            or not isinstance(item.get("stage_version"), str)
        ):
            raise ValueError("Architect Pipeline Stage identity is malformed")
    return stages, stages[:-1], stages[-1]


def _discover_prefix(folder: Path, manifest: dict[str, Any]):
    folder = Path(folder)
    files = sorted(
        (
            path
            for path in folder.iterdir()
            if path.is_file() and path.suffix.casefold() == ".json"
        ),
        key=lambda path: path.name.casefold(),
    )
    if not files:
        raise ValueError("prefix Stage Output folder is empty")

    stages, nonterminal, terminal = _manifest_prefix_authority(manifest)
    inventory = {item["stage_id"] for item in stages}
    seen: dict[str, tuple[Path, dict[str, Any]]] = {}
    for path in files:
        value = load_strict(path)
        for key in ("stage_id", "stage_version", "run_id"):
            if not isinstance(value.get(key), str) or not value[key]:
                raise ValueError(f"{path.name}: missing required identity field {key}")
        stage_id = value["stage_id"]
        if stage_id in seen:
            raise ValueError(
                f"duplicate Stage Output for {stage_id}: "
                f"{seen[stage_id][0].name}, {path.name}"
            )
        seen[stage_id] = (path, value)

    unknown = sorted(set(seen) - inventory)
    if unknown:
        raise ValueError(f"unknown Stage Output(s): {', '.join(unknown)}")
    if terminal["stage_id"] in seen:
        raise ValueError(
            f"terminal Stage Output is not accepted in a prefix: {terminal['stage_id']}"
        )
    if len(seen) > len(nonterminal):
        raise ValueError("prefix contains too many Stage Outputs")

    expected = nonterminal[: len(seen)]
    expected_ids = [item["stage_id"] for item in expected]
    observed_ids = set(seen)
    if observed_ids != set(expected_ids):
        rendered_observed = ", ".join(sorted(observed_ids)) or "(none)"
        raise ValueError(
            "Stage Outputs must form the exact leading Manifest prefix; "
            f"expected: {', '.join(expected_ids)}; observed: {rendered_observed}"
        )

    ordered = [seen[item["stage_id"]] for item in expected]
    run_ids = {value["run_id"] for _, value in ordered}
    if len(run_ids) != 1:
        raise ValueError("Stage Outputs do not share one run_id")
    for stage, (path, value) in zip(expected, ordered, strict=True):
        if value["stage_version"] != stage["stage_version"]:
            raise ValueError(f"{path.name}: stage version does not match manifest")
    return ordered


def _snapshot(attempt, entries, terminal=None):
    copied = []
    for path, _ in entries:
        target = attempt / "input-snapshot" / path.name
        shutil.copyfile(path, target)
        if target.read_bytes() != path.read_bytes():
            target.unlink(missing_ok=True)
            raise ValueError(f"Stage Output snapshot bytes differ: {path.name}")
        copied.append((target, load_strict(target)))
    if terminal:
        target = attempt / "input-snapshot" / f"terminal-{terminal.name}"
        shutil.copyfile(terminal, target)
        if target.read_bytes() != terminal.read_bytes():
            target.unlink(missing_ok=True)
            raise ValueError(f"Stage Output snapshot bytes differ: {terminal.name}")
        terminal = load_strict(target)
    return copied, terminal


def _run_context(connection, source_kind: str):
    return connection.runtime.RunContext(source_kind=source_kind)


def _terminal_instruction() -> str:
    return TERMINAL_INSTRUCTION


def _context(connection, outputs, run, source_kind: str):
    state = run["run_state"]
    by_stage = {value["stage_id"]: value for value in outputs}
    return {
        "context_schema_version": "2.0",
        "runtime_interface_id": connection.runtime_interface_id,
        "execution_context": {
            "source_kind": source_kind,
            "synthetic": source_kind != "live_conversation",
        },
        "authority": {
            "repository": "rezahh107/EV4-Architect-Repo",
            "commit": connection.commit,
            "observed_commit_sha": connection.commit,
            "reference_commit_sha": connection.reference_commit,
            "compatibility_mode": connection.compatibility_mode,
            "authority_files_verified": len(connection.identities),
            "ref": connection.ref,
            "committed_blob_oids": connection.identities,
        },
        "receipt": {
            "run_status": run["status"],
            "stages_visited": run["stages_visited"],
            "semantic_digest": canonical_sha256(run),
        },
        "run_state": state,
        "stage_results": run["results"],
        "validated_stage_outputs": outputs,
        "selected_candidate_identity": state.get("selected_candidate_id"),
        "candidate_lock_state": state.get("selected_candidate_locked"),
        "build_tree_content": by_stage.get("/build-tree", {}).get("canonical_content"),
        "implementation_content": by_stage.get("/implementation", {}).get("canonical_content"),
        "active_and_resolved_unknowns": state.get("unknown_ledger", []),
        "final_audit_findings": by_stage.get("/final-audit", {}).get("final_audit_findings", []),
        "handoff_export_content": by_stage.get("/handoff-export", {}),
        "terminal_stage": {
            "stage_id": "/project-gate-export",
            "stage_version": connection.runtime.load_authority(connection.path)[0][
                "project_execution_stages"
            ][-1]["stage_version"],
        },
        "instruction": _terminal_instruction(),
        "content_identities": {
            "stage_outputs_canonical_sha256": canonical_sha256(outputs),
            "run_canonical_sha256": canonical_sha256(run),
        },
    }


def _runtime_module_origins(connection) -> dict[str, str]:
    modules = {
        "wrapper_origin": connection.runtime,
        "package_origin": sys.modules.get("architect_quality_runtime"),
        "history_origin": sys.modules.get("architect_quality_runtime.history"),
        "finalization_origin": sys.modules.get(
            "architect_project_gate_finalization"
        ),
    }
    expected = {
        "wrapper_origin": connection.path / "scripts/architect_quality_runtime.py",
        "package_origin": (
            connection.path / "scripts/architect_quality_runtime/__init__.py"
        ),
        "history_origin": (
            connection.path / "scripts/architect_quality_runtime/history.py"
        ),
        "finalization_origin": (
            connection.path / "scripts/architect_project_gate_finalization.py"
        ),
    }
    origins: dict[str, str] = {}
    for field, module in modules.items():
        raw = getattr(module, "__file__", None)
        if not isinstance(raw, str) or not raw:
            raise ValueError(f"Architect Runtime module origin is unavailable: {field}")
        observed = Path(raw).resolve(strict=True)
        if observed != expected[field].resolve(strict=True):
            raise ValueError(
                f"Architect Runtime module origin does not match selected checkout: {field}"
            )
        origins[field] = str(observed)
    return origins


def _authority_projection(connection) -> dict[str, Any]:
    return {
        "repository": "rezahh107/EV4-Architect-Repo",
        "observed_commit_sha": connection.commit,
        "reference_commit_sha": connection.reference_commit,
        "compatibility_mode": connection.compatibility_mode,
        "runtime_interface_id": connection.runtime_interface_id,
        "authority_files_verified": len(connection.identities),
        "ref": connection.ref,
        "runtime_module_origins": _runtime_module_origins(connection),
    }


def _input_identities(
    entries: list[tuple[Path, dict[str, Any]]],
    snapshot: list[tuple[Path, dict[str, Any]]],
) -> list[dict[str, Any]]:
    result = []
    for (source, source_value), (copied, copied_value) in zip(
        entries, snapshot, strict=True
    ):
        source_digest = raw_sha256(source)
        copied_digest = raw_sha256(copied)
        if source_digest != copied_digest or source_value != copied_value:
            raise ValueError(f"Stage Output snapshot identity differs: {source.name}")
        result.append(
            {
                "stage_id": copied_value["stage_id"],
                "source_filename": source.name,
                "snapshot_filename": copied.name,
                "raw_sha256": source_digest,
                "canonical_json_sha256": canonical_sha256(copied_value),
            }
        )
    return result


def _valid_run_state(
    value: Any,
    *,
    run_id: str,
    completed_stages: list[str],
    current_stage: str,
    evaluated_stage_outputs: list[dict[str, Any]],
    derived_stage_results: list[dict[str, Any]],
) -> bool:
    return (
        isinstance(value, dict)
        and RUN_STATE_FIELDS <= set(value)
        and value.get("run_id") == run_id
        and value.get("completed_stages") == completed_stages
        and value.get("current_stage") == current_stage
        and isinstance(value.get("unknown_ledger"), list)
        and isinstance(value.get("selected_candidate_locked"), bool)
        and value.get("evaluated_stage_outputs") == evaluated_stage_outputs
        and value.get("derived_stage_results") == derived_stage_results
    )


def _prefix_repair_plan(
    *,
    blocking_issues: list[dict[str, Any]],
    observed_stage_ids: list[str],
    failed_stage: str,
    manifest: dict[str, Any],
) -> tuple[dict[str, Any] | None, str]:
    stages, _, _ = _manifest_prefix_authority(manifest)
    manifest_stage_ids = [item["stage_id"] for item in stages]
    manifest_positions = {
        stage_id: index for index, stage_id in enumerate(manifest_stage_ids)
    }
    if (
        not observed_stage_ids
        or failed_stage not in manifest_positions
        or failed_stage != observed_stage_ids[-1]
        or any(stage_id not in manifest_positions for stage_id in observed_stage_ids)
        or observed_stage_ids
        != manifest_stage_ids[: len(observed_stage_ids)]
    ):
        return (
            None,
            "Runtime repair plan cannot be projected from the observed Manifest prefix.",
        )

    failed_position = manifest_positions[failed_stage]
    target_stage_ids: set[str] = set()
    for issue in blocking_issues:
        repair_stage = issue.get("repair_stage")
        if not isinstance(repair_stage, str) or not repair_stage:
            return (
                None,
                "Runtime repair plan contains a null or malformed repair_stage.",
            )
        if repair_stage not in manifest_positions:
            return (
                None,
                "Runtime repair plan contains a repair_stage unknown to the loaded Manifest.",
            )
        if manifest_positions[repair_stage] > failed_position:
            return (
                None,
                "Runtime repair plan contains a repair_stage later than the failed Stage.",
            )
        if repair_stage not in observed_stage_ids:
            return (
                None,
                "Runtime repair plan contains a repair_stage outside the observed prefix.",
            )
        target_stage_ids.add(repair_stage)

    ordered_repair_stage_ids = [
        stage_id
        for stage_id in observed_stage_ids
        if stage_id in target_stage_ids
    ]
    if not ordered_repair_stage_ids:
        return None, "Runtime repair plan contains no actionable repair_stage."
    earliest_repair_stage = ordered_repair_stage_ids[0]
    earliest_position = observed_stage_ids.index(earliest_repair_stage)
    return (
        {
            "ordered_repair_stage_ids": ordered_repair_stage_ids,
            "earliest_repair_stage": earliest_repair_stage,
            "retained_stage_ids": observed_stage_ids[:earliest_position],
            "invalidated_stage_ids": observed_stage_ids[earliest_position:],
            "runtime_replay_required": True,
        },
        "",
    )


def _classify_prefix_result(
    run: Any,
    outputs: list[dict[str, Any]],
    manifest: dict[str, Any],
    result_schema: dict[str, Any],
) -> tuple[str | None, str, dict[str, Any] | None]:
    if not isinstance(run, dict) or set(run) != PREFIX_RESULT_FIELDS:
        return (
            None,
            "Runtime result does not match the verified top-level contract.",
            None,
        )
    if run.get("status") not in {"valid", "invalid"}:
        return None, "Runtime returned an unknown top-level status.", None
    if (
        not isinstance(run.get("errors"), list)
        or any(not isinstance(item, str) for item in run["errors"])
        or not isinstance(run.get("diagnostics"), list)
        or any(
            not isinstance(item, dict)
            or set(item) != {"code", "message", "path", "stage_id"}
            or not isinstance(item.get("code"), str)
            or not isinstance(item.get("message"), str)
            for item in run["diagnostics"]
        )
        or not isinstance(run.get("results"), list)
        or not isinstance(run.get("stages_visited"), list)
        or any(not isinstance(item, str) for item in run["stages_visited"])
        or not isinstance(run.get("all_required_stages_visited"), bool)
    ):
        return None, "Runtime result fields are malformed.", None

    _, _, terminal = _manifest_prefix_authority(manifest)
    results = run["results"]
    if (
        len(results) != len(outputs)
        or not results
        or run["terminal_stage"] != terminal["stage_id"]
    ):
        return (
            None,
            "Runtime did not return one bounded Stage Result per input.",
            None,
        )
    validator = Draft202012Validator(result_schema)
    if any(list(validator.iter_errors(item)) for item in results):
        return (
            None,
            "Runtime returned a Stage Result that fails its public Schema.",
            None,
        )
    result_stage_ids = [item["stage_id"] for item in results]
    output_stage_ids = [item["stage_id"] for item in outputs]
    if (
        result_stage_ids != output_stage_ids
        or run["stages_visited"] != result_stage_ids
        or any(
            result["run_id"] != output["run_id"]
            or result["stage_version"] != output["stage_version"]
            for result, output in zip(results, outputs, strict=True)
        )
    ):
        return None, "Runtime Stage Result identity does not match the prefix.", None

    last = results[-1]
    prior_ids = output_stage_ids[:-1]
    if any(item.get("stage_status") != "pass" for item in results[:-1]):
        return (
            None,
            "Runtime returned results after an unsuccessful prior Stage.",
            None,
        )

    stages, _, _ = _manifest_prefix_authority(manifest)
    by_stage = {item["stage_id"]: item for item in stages}
    successor = by_stage[last["stage_id"]].get("next_stage")
    status = last.get("stage_status")
    if status == "pass":
        if (
            run["status"] != "valid"
            or run["errors"]
            or run["diagnostics"]
            or not isinstance(last.get("next_stage"), str)
            or last["next_stage"] != successor
            or not _valid_run_state(
                run.get("run_state"),
                run_id=outputs[0]["run_id"],
                completed_stages=output_stage_ids,
                current_stage=last["next_stage"],
                evaluated_stage_outputs=outputs,
                derived_stage_results=results,
            )
        ):
            return (
                None,
                "Runtime pass result, successor, and Run State are inconsistent.",
                None,
            )
        return "PREFIX_VALID", "", None

    if status in {"needs_input", "blocked"}:
        if (
            run["status"] != "invalid"
            or last.get("next_stage") is not None
            or not last.get("blocking_issues")
            or not run["diagnostics"]
            or not _valid_run_state(
                run.get("run_state"),
                run_id=outputs[0]["run_id"],
                completed_stages=prior_ids,
                current_stage=last["stage_id"],
                evaluated_stage_outputs=outputs[:-1],
                derived_stage_results=results[:-1],
            )
        ):
            return (
                None,
                "Runtime unsuccessful result and preserved Run State are inconsistent.",
                None,
            )
        repair_plan, repair_plan_error = _prefix_repair_plan(
            blocking_issues=last["blocking_issues"],
            observed_stage_ids=output_stage_ids,
            failed_stage=last["stage_id"],
            manifest=manifest,
        )
        if repair_plan is None:
            return None, repair_plan_error, None
        return (
            "PREFIX_NEEDS_INPUT"
            if status == "needs_input"
            else "PREFIX_BLOCKED",
            "",
            repair_plan,
        )

    return None, "Runtime returned an unrecognized Stage status.", None


def _runtime_diagnostics(run: Any) -> str:
    if not isinstance(run, dict):
        return "Runtime result is unavailable or malformed."
    values = []
    for item in run.get("diagnostics", []):
        if not isinstance(item, dict):
            continue
        code = item.get("code")
        message = item.get("message")
        if code and message:
            values.append(f"{code}: {message}")
        elif code or message:
            values.append(str(code or message))
    values.extend(
        item for item in run.get("errors", []) if isinstance(item, str)
    )
    return "; ".join(dict.fromkeys(values)) or "Runtime evaluation was unclassified."


def _stage_output_schema(connection) -> dict[str, Any]:
    schema = load_strict(connection.path / STAGE_OUTPUT_SCHEMA_PATH)
    if schema.get("$id") != "ev4-architect-conversational-stage-output-base@1.0.0":
        raise ValueError("Architect Stage Output Schema identity is unexpected")
    forbidden = (
        schema.get("$defs", {})
        .get("caller_authority_field", {})
        .get("enum")
    )
    if not isinstance(forbidden, list) or any(
        not isinstance(item, str) for item in forbidden
    ):
        raise ValueError("Architect Stage Output forbidden-field contract is malformed")
    return schema


def _next_stage_instruction(next_stage: str) -> str:
    return (
        f"Generate exactly one {next_stage} Stage Output for this Run using the "
        "supplied immutable prefix, evaluator-derived Run State, Manifest identity, "
        "required quality-check keys, and exact Stage Output Schema boundary. Do not "
        "author any field listed in next_stage_schema.forbidden_top_level_fields or "
        "any Runtime-owned Stage Result, Run State, Runtime Context, official digest, "
        "Payload, eligibility, Project Gate finalization, Handoff Boolean, execution "
        "provenance, or producer provenance claim. Stage-owned evidence references, "
        "Unknown introductions or resolutions, resolution metadata, research "
        "disposition, canonical content, and Stage-specific evidence remain permitted "
        "when the Stage contract and Schema allow them."
    )


def _prefix_receipt(
    connection,
    *,
    code: str,
    outputs: list[dict[str, Any]],
    identities: list[dict[str, Any]],
    run: dict[str, Any],
) -> dict[str, Any]:
    return {
        "receipt_schema": "ev4-architect-prefix-qc-receipt@1.0.0",
        "operation": "validate_current_pipeline_prefix",
        "runtime_interface_id": connection.runtime_interface_id,
        "outcome": {"code": code, "success": code == "PREFIX_VALID"},
        "authority": _authority_projection(connection),
        "run_id": outputs[0]["run_id"],
        "validated_stage_ids": [item["stage_id"] for item in outputs],
        "input_file_identities": identities,
        "runtime_result_canonical_sha256": canonical_sha256(run),
        "evidence_boundary": dict(EVIDENCE_BOUNDARY),
    }


def _next_stage_context(
    connection,
    *,
    outputs: list[dict[str, Any]],
    identities: list[dict[str, Any]],
    run: dict[str, Any],
    manifest: dict[str, Any],
    source_kind: str,
) -> dict[str, Any]:
    state = run["run_state"]
    next_stage = run["results"][-1]["next_stage"]
    stages, _, _ = _manifest_prefix_authority(manifest)
    stage = next(item for item in stages if item["stage_id"] == next_stage)
    schema = _stage_output_schema(connection)
    forbidden = schema["$defs"]["caller_authority_field"]["enum"]
    instruction = (
        _terminal_instruction()
        if next_stage == manifest["final_project_gate_export_stage"]
        else _next_stage_instruction(next_stage)
    )
    return {
        "context_id": "ev4-architect-next-stage-context",
        "context_version": PREFIX_CONTEXT_VERSION,
        "runtime_interface_id": connection.runtime_interface_id,
        "execution_context": {
            "source_kind": source_kind,
            "synthetic": source_kind != "live_conversation",
        },
        "authority": _authority_projection(connection),
        "run_id": outputs[0]["run_id"],
        "validated_stage_ids": [item["stage_id"] for item in outputs],
        "input_snapshot": outputs,
        "input_file_identities": identities,
        "stage_results": run["results"],
        "run_state": state,
        "active_and_resolved_unknowns": state["unknown_ledger"],
        "selected_candidate_id": state.get("selected_candidate_id"),
        "selected_candidate_locked": state["selected_candidate_locked"],
        "current_stage": state["current_stage"],
        "next_stage": next_stage,
        "next_stage_version": stage["stage_version"],
        "next_stage_required_quality_checks": stage["required_quality_checks"],
        "next_stage_schema": {
            "schema_id": schema["$id"],
            "schema_path": STAGE_OUTPUT_SCHEMA_PATH,
            "required_common_fields": schema["required"],
            "forbidden_top_level_fields": forbidden,
            "stage_owned_extension_fields_permitted": (
                schema.get("additionalProperties") is True
            ),
        },
        "instruction": instruction,
        "evidence_boundary": dict(EVIDENCE_BOUNDARY),
    }


def _active_unknowns(stage_result: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        item
        for item in stage_result.get("carried_unknowns", [])
        if isinstance(item, dict) and item.get("status") == "active"
    ]


def _input_context(
    connection,
    *,
    outputs: list[dict[str, Any]],
    identities: list[dict[str, Any]],
    run: dict[str, Any],
    repair_plan: dict[str, Any],
    source_kind: str,
) -> dict[str, Any]:
    result = run["results"][-1]
    return {
        "context_id": "ev4-architect-prefix-input-context",
        "context_version": PREFIX_CONTEXT_VERSION,
        "runtime_interface_id": connection.runtime_interface_id,
        "execution_context": {
            "source_kind": source_kind,
            "synthetic": source_kind != "live_conversation",
        },
        "authority": _authority_projection(connection),
        "run_id": outputs[0]["run_id"],
        "affected_stage": result["stage_id"],
        "repair_plan": repair_plan,
        "needs_input_stage_result": result,
        "official_blocking_issues": result["blocking_issues"],
        "evaluator_returned_prior_preserved_run_state": run["run_state"],
        "active_unknowns_relevant_to_missing_input": _active_unknowns(result),
        "input_snapshot": outputs,
        "input_file_identities": identities,
        "instruction": (
            "Supply only the missing user or source evidence identified by "
            "official_blocking_issues. Modify only the Stage Output named by "
            "repair_plan.earliest_repair_stage. Do not reuse any Stage Output listed "
            "in repair_plan.invalidated_stage_ids and do not continue to a later "
            "Stage. No continuation is authorized. After the repair, rerun current "
            "Pipeline prefix validation. "
            "affected_stage identifies the failed evaluated Stage and may differ "
            "from the repair target. The supplied Run State is evaluator-returned "
            "prior/preserved context, not acceptance of the failed Stage."
        ),
        "evidence_boundary": dict(EVIDENCE_BOUNDARY),
    }


def _repair_context(
    connection,
    *,
    outputs: list[dict[str, Any]],
    identities: list[dict[str, Any]],
    run: dict[str, Any],
    repair_plan: dict[str, Any],
    source_kind: str,
) -> dict[str, Any]:
    result = run["results"][-1]
    return {
        "context_id": "ev4-architect-prefix-repair-context",
        "context_version": PREFIX_CONTEXT_VERSION,
        "runtime_interface_id": connection.runtime_interface_id,
        "execution_context": {
            "source_kind": source_kind,
            "synthetic": source_kind != "live_conversation",
        },
        "authority": _authority_projection(connection),
        "run_id": outputs[0]["run_id"],
        "affected_stage": result["stage_id"],
        "repair_plan": repair_plan,
        "blocked_stage_result": result,
        "official_blocking_issues": result["blocking_issues"],
        "evaluator_returned_prior_preserved_run_state": run["run_state"],
        "input_snapshot": outputs,
        "input_file_identities": identities,
        "instruction": (
            "Repair only the Stage Output named by "
            "repair_plan.earliest_repair_stage according to "
            "official_blocking_issues. Do not reuse any Stage Output listed in "
            "repair_plan.invalidated_stage_ids and do not continue to a later "
            "Stage. No continuation is authorized. After the repair, rerun current "
            "Pipeline prefix validation. "
            "affected_stage identifies the failed evaluated Stage and may differ "
            "from the repair target. Stage-QC has not independently reinterpreted "
            "the failure."
        ),
        "evidence_boundary": dict(EVIDENCE_BOUNDARY),
    }


def _copy_verified(source: Path, destination: Path) -> None:
    source_bytes = source.read_bytes()
    atomic_write(destination, source_bytes)
    if destination.read_bytes() != source_bytes:
        destination.unlink(missing_ok=True)
        raise ValueError(f"Copied Architect artifact bytes differ: {source.name}")


def run_prefix_validation(
    stage_folder: Path,
    architect_root: Path,
    *,
    source_kind: str = "live_conversation",
):
    attempt = _attempt(Path(stage_folder))
    try:
        connection = verify(architect_root)
        if not connection.ok:
            return _record(
                attempt,
                "ARCHITECT_CONNECTION_INVALID",
                connection.reason,
                "Select a compatible local Architect repository.",
            )

        manifest, result_schema = connection.runtime.load_authority(connection.path)
        entries = _discover_prefix(Path(stage_folder), manifest)
        snapshot, _ = _snapshot(attempt, entries)
        outputs = [value for _, value in snapshot]
        identities = _input_identities(entries, snapshot)
        run = connection.runtime.evaluate_run(
            outputs,
            root=connection.path,
            require_terminal=False,
            run_context=_run_context(connection, source_kind),
        )
        code, classification_error, repair_plan = _classify_prefix_result(
            run,
            outputs,
            manifest,
            result_schema,
        )
        if code is None:
            reason = (
                classification_error
                if classification_error.startswith("Runtime repair plan")
                else "; ".join(
                    item
                    for item in (classification_error, _runtime_diagnostics(run))
                    if item
                )
            )
            return _record(
                attempt,
                "PREFIX_EVALUATION_UNCLASSIFIED",
                reason,
                "Review diagnostics; no model-action context was issued.",
            )

        receipt = _prefix_receipt(
            connection,
            code=code,
            outputs=outputs,
            identities=identities,
            run=run,
        )
        artifacts: dict[str, Any] = {
            "architect-prefix-qc-receipt.json": receipt,
            "architect-prefix-stage-results.json": run["results"],
            "architect-prefix-run-state.json": run["run_state"],
        }
        if code == "PREFIX_VALID":
            artifacts["architect-next-stage-context.json"] = _next_stage_context(
                connection,
                outputs=outputs,
                identities=identities,
                run=run,
                manifest=manifest,
                source_kind=source_kind,
            )
            success = True
            reason = "Official Runtime accepted the current Pipeline prefix."
            action = (
                "Give architect-next-stage-context.json to the model for the "
                "exact next Stage."
            )
        elif code == "PREFIX_NEEDS_INPUT":
            artifacts["architect-prefix-input-context.json"] = _input_context(
                connection,
                outputs=outputs,
                identities=identities,
                run=run,
                repair_plan=repair_plan,
                source_kind=source_kind,
            )
            success = False
            reason = "Official Runtime requires bounded input for the affected Stage."
            action = (
                "Use architect-prefix-input-context.json to obtain only the "
                "missing evidence."
            )
        else:
            artifacts["architect-prefix-repair-context.json"] = _repair_context(
                connection,
                outputs=outputs,
                identities=identities,
                run=run,
                repair_plan=repair_plan,
                source_kind=source_kind,
            )
            success = False
            reason = "Official Runtime blocked the affected Stage."
            action = (
                "Give architect-prefix-repair-context.json to the model to "
                "repair only the affected Stage Output."
            )

        for name, value in artifacts.items():
            write_json(attempt / "generated-artifacts" / name, value)
        return _record(
            attempt,
            code,
            reason,
            action,
            success,
            artifacts,
        )
    except (OSError, ValueError) as exc:
        return _record(
            attempt,
            "PREFIX_INPUT_INVALID",
            str(exc),
            "Correct the Stage Output folder and run again.",
        )
    except Exception:
        return _record(
            attempt,
            "PREFIX_EVALUATION_UNCLASSIFIED",
            "Runtime evaluation failed before a safe prefix outcome could be classified.",
            "Review diagnostics; no model-action context was issued.",
        )


def run_prefinal_validation(
    stage_folder: Path,
    architect_root: Path,
    *,
    source_kind: str = "live_conversation",
):
    attempt = _attempt(Path(stage_folder))
    connection = verify(architect_root)
    if not connection.ok:
        return _record(
            attempt,
            "ARCHITECT_CONNECTION_INVALID",
            connection.reason,
            "Select a compatible local Architect repository.",
        )
    try:
        manifest, _ = connection.runtime.load_authority(connection.path)
        entries = _discover(Path(stage_folder), manifest)
        snapshot, _ = _snapshot(attempt, entries)
        outputs = [value for _, value in snapshot]
        run = connection.runtime.evaluate_run(
            outputs,
            root=connection.path,
            require_terminal=False,
            run_context=_run_context(connection, source_kind),
        )
        if run["status"] != "valid":
            return _record(
                attempt,
                "PREFINAL_EVALUATION_FAILED",
                "; ".join(run["errors"]),
                "Repair the reported Stage Output and run again.",
            )
        context = _context(connection, outputs, run, source_kind)
        artifacts = {
            "architect-prefinal-qc-receipt.json": context["receipt"],
            "architect-prefinal-run-state.json": run["run_state"],
            "architect-prefinal-stage-results.json": run["results"],
            "architect-final-stage-context.json": context,
        }
        for name, value in artifacts.items():
            write_json(attempt / "generated-artifacts" / name, value)
        return _record(
            attempt,
            "PREFINAL_VALID",
            "Official prefinal evaluation passed.",
            "Give architect-final-stage-context.json to the model.",
            True,
            artifacts,
        )
    except (OSError, ValueError) as exc:
        return _record(
            attempt,
            "PREFINAL_INPUT_INVALID",
            str(exc),
            "Correct the Stage Output folder and run again.",
        )


def run_final_validation(
    stage_folder: Path,
    terminal_path: Path,
    architect_root: Path,
    *,
    source_kind: str = "live_conversation",
):
    attempt = _attempt(Path(stage_folder))
    connection = verify(architect_root)
    if not connection.ok:
        return _record(
            attempt,
            "ARCHITECT_CONNECTION_INVALID",
            connection.reason,
            "Select a compatible local Architect repository.",
        )
    try:
        manifest, _ = connection.runtime.load_authority(connection.path)
        entries = _discover(Path(stage_folder), manifest, {Path(terminal_path)})
        snapshot, terminal = _snapshot(attempt, entries, Path(terminal_path))
        outputs = [value for _, value in snapshot]
        expected = manifest["project_execution_stages"][-1]
        if terminal.get("run_id") != outputs[0]["run_id"]:
            raise ValueError("terminal run_id does not match prefinal run")
        if (
            terminal.get("stage_id") != expected["stage_id"]
            or terminal.get("stage_version") != expected["stage_version"]
        ):
            raise ValueError("terminal Stage identity/version does not match manifest")

        publication_dir = attempt / "architect-publication"
        finalization = connection.runtime.finalize_project_gate(
            [*outputs, terminal],
            run_context=_run_context(connection, source_kind),
            repository_root=connection.path,
            output_directory=publication_dir,
        )
        if not finalization.finalization_succeeded:
            messages = [
                _diagnostic_text(item)
                for item in finalization.diagnostics
                if isinstance(item, dict)
            ]
            return _record(
                attempt,
                "FINALIZATION_FAILED",
                "; ".join(messages) or "Architect Runtime finalization failed.",
                "Repair the terminal Stage Output or upstream evidence and run again.",
            )
        if not finalization.artifact_path or not finalization.receipt_path:
            raise ValueError("Architect Runtime did not return publication paths")

        generated = attempt / "generated-artifacts"
        artifact_target = generated / "architect-project-gate.json"
        receipt_target = generated / "architect-project-gate-receipt.json"
        _copy_verified(Path(finalization.artifact_path), artifact_target)
        _copy_verified(Path(finalization.receipt_path), receipt_target)
        write_json(generated / "architect-final-run-state.json", finalization.run_state)
        write_json(
            generated / "architect-final-stage-results.json",
            list(finalization.stage_results),
        )
        artifacts = (
            "architect-project-gate.json",
            "architect-project-gate-receipt.json",
            "architect-final-run-state.json",
            "architect-final-stage-results.json",
        )
        if not finalization.handoff_allowed:
            return _record(
                attempt,
                "FINAL_HANDOFF_BLOCKED",
                f"Architect finalization completed as {finalization.publication_status}; handoff remains blocked.",
                "Resolve the Runtime-derived blockers before downstream handoff.",
                False,
                artifacts,
            )
        return _record(
            attempt,
            "FINAL_VALID",
            "Architect Runtime finalized and published the official Project Gate artifact and receipt.",
            "Open the final result folder.",
            True,
            artifacts,
        )
    except (OSError, ValueError) as exc:
        return _record(
            attempt,
            "FINAL_INPUT_INVALID",
            str(exc),
            "Correct the input files and run again.",
        )
