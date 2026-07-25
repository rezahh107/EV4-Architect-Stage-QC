from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path

from .architect_adapter import verify
from .json_io import atomic_write, canonical_sha256, load_strict, write_json
from .models import CoreResult

APP_VERSION = "0.3.0"


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


def _snapshot(attempt, entries, terminal=None):
    copied = []
    for path, _ in entries:
        target = attempt / "input-snapshot" / path.name
        shutil.copyfile(path, target)
        copied.append((target, load_strict(target)))
    if terminal:
        target = attempt / "input-snapshot" / f"terminal-{terminal.name}"
        shutil.copyfile(terminal, target)
        terminal = load_strict(target)
    return copied, terminal


def _run_context(connection, source_kind: str):
    return connection.runtime.RunContext(source_kind=source_kind)


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
        "instruction": (
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
        ),
        "content_identities": {
            "stage_outputs_canonical_sha256": canonical_sha256(outputs),
            "run_canonical_sha256": canonical_sha256(run),
        },
    }


def _copy_verified(source: Path, destination: Path) -> None:
    source_bytes = source.read_bytes()
    atomic_write(destination, source_bytes)
    if destination.read_bytes() != source_bytes:
        destination.unlink(missing_ok=True)
        raise ValueError(f"Copied Architect artifact bytes differ: {source.name}")


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
