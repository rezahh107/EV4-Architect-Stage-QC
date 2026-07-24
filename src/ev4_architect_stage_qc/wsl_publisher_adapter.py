"""Thin Windows transport only; it never performs Git or exporter work."""
from __future__ import annotations
import subprocess
from pathlib import Path
from .publication_contracts import PublicationRequest, PublicationResponse

def worker_command(wsl_exe: str, distribution: str, request_path: Path, response_path: Path) -> list[str]:
    return [wsl_exe, "--distribution", distribution, "--exec", "python3", "-m", "ev4_architect_stage_qc.wsl_publisher_worker", "--request", str(request_path), "--response", str(response_path)]

def invoke(request: PublicationRequest, *, distribution: str, request_path: Path, response_path: Path, wsl_exe: str = "wsl.exe") -> PublicationResponse:
    process = subprocess.run(worker_command(wsl_exe, distribution, request_path, response_path), capture_output=True, text=True, check=False)
    if not response_path.is_file():
        raise RuntimeError(f"WSL publisher transport failed (exit {process.returncode}): response is missing; {process.stderr.strip()}")
    # Response validation intentionally remains strict at the trust boundary.
    raw = response_path.read_text("utf-8")
    import json
    value = json.loads(raw)
    if value.get("schema_version") != "1.0" or value.get("request_id") != request.request_id:
        raise ValueError("WSL publisher response identity is invalid")
    from .publication_lifecycle import PublicationOutcome, PublicationState
    outcome = PublicationOutcome(state=PublicationState(value["state"]), artifact_committed=value["artifact_committed"], output_committed=value["output_committed"], handoff_allowed_by_receipt=value["handoff_allowed_by_receipt"], handoff_trusted_by_qc=value["handoff_trusted_by_qc"], current_revision_accepted=value["current_revision_accepted"], canonical_destination_present=value["canonical_destination_present"], official_export_exit_code=value["official_export_exit_code"])
    return PublicationResponse(request.request_id, outcome, value.get("wsl_distribution"), value["architect_commit"])
