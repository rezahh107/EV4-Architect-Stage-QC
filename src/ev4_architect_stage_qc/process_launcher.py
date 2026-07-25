from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .models import ConnectionResult, CoreResult, ExecutionIdentity

PROTOCOL_VERSION = "1.0"
CHILD_MODULE = "ev4_architect_stage_qc.process_child"
MAX_REQUEST_BYTES = 64 * 1024
MAX_RESULT_BYTES = 1024 * 1024
PROCESS_TIMEOUT_SECONDS = 3600
_COMMON_RESULT_FIELDS = {
    "protocol_version",
    "request_id",
    "operation",
    "child_pid",
    "status",
    "execution_identity",
}
_VERIFY_RESULT_FIELDS = {
    "connection_ok",
    "connection_reason",
    "actual_commit",
    "reference_commit",
    "runtime_interface_id",
    "compatibility_mode",
    "authority_file_count",
    "ref",
}
_VALIDATION_RESULT_FIELDS = {
    "success",
    "attempt_path",
    "code",
    "reason",
    "next_action",
    "artifacts",
}
_ERROR_RESULT_FIELDS = {"error_code", "error_message"}


@dataclass(frozen=True)
class ChildProcessFailure(RuntimeError):
    code: str
    reason: str
    child_pid: int | None = None

    def __str__(self) -> str:
        return f"{self.code}: {self.reason}"


def sibling_checkout() -> Path | None:
    path = Path.cwd().parent / "EV4-Architect-Repo"
    return path if path.is_dir() else None


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _load_json(path: Path, limit: int) -> dict[str, Any]:
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise ChildProcessFailure(
            "CHILD_RESULT_MISSING",
            "Child result file is unavailable.",
        ) from exc
    if len(data) > limit:
        raise ChildProcessFailure(
            "CHILD_RESULT_MALFORMED",
            "Child result exceeds the bounded size limit.",
        )
    try:
        value = json.loads(data.decode("utf-8"), object_pairs_hook=_strict_object)
    except (UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise ChildProcessFailure(
            "CHILD_RESULT_MALFORMED",
            "Child result is not strict UTF-8 JSON.",
        ) from exc
    if not isinstance(value, dict):
        raise ChildProcessFailure(
            "CHILD_RESULT_MALFORMED",
            "Child result must be a JSON object.",
        )
    return value


def _atomic_json_write(path: Path, value: dict[str, Any], limit: int) -> None:
    data = (
        json.dumps(
            value,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")
    if len(data) > limit:
        raise ValueError("JSON transport document exceeds its bounded size limit.")
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    try:
        temporary.write_bytes(data)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _bounded_text(value: Any, field: str, *, nullable: bool = False) -> str | None:
    if value is None and nullable:
        return None
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 32768
        or "\x00" in value
    ):
        raise ChildProcessFailure(
            "CHILD_RESULT_SCHEMA_INVALID",
            f"Child result field is invalid: {field}",
        )
    return value


def _decode_identity(value: Any) -> ExecutionIdentity | None:
    if value is None:
        return None
    expected = {
        "wrapper_origin",
        "package_origin",
        "history_origin",
        "finalization_origin",
    }
    if not isinstance(value, dict) or set(value) != expected:
        raise ChildProcessFailure(
            "CHILD_RESULT_SCHEMA_INVALID",
            "Child execution identity is malformed.",
        )
    decoded: dict[str, Path | None] = {}
    for field in sorted(expected):
        raw = value[field]
        if raw is None:
            decoded[field] = None
        else:
            decoded[field] = Path(_bounded_text(raw, field))
    return ExecutionIdentity(**decoded)


def _validate_common(
    result: dict[str, Any],
    *,
    request_id: str,
    operation: str,
) -> tuple[int, ExecutionIdentity | None]:
    if result.get("protocol_version") != PROTOCOL_VERSION:
        raise ChildProcessFailure(
            "CHILD_RESULT_IDENTITY_MISMATCH",
            "Child protocol version mismatch.",
        )
    if result.get("request_id") != request_id:
        raise ChildProcessFailure(
            "CHILD_RESULT_IDENTITY_MISMATCH",
            "Child request identity mismatch.",
        )
    if result.get("operation") != operation:
        raise ChildProcessFailure(
            "CHILD_RESULT_IDENTITY_MISMATCH",
            "Child operation identity mismatch.",
        )
    child_pid = result.get("child_pid")
    if not isinstance(child_pid, int) or isinstance(child_pid, bool) or child_pid <= 0:
        raise ChildProcessFailure(
            "CHILD_RESULT_SCHEMA_INVALID",
            "Child PID is invalid.",
        )
    status = result.get("status")
    if status not in {"completed", "error"}:
        raise ChildProcessFailure(
            "CHILD_RESULT_STATUS_INVALID",
            "Child returned an unknown status.",
            child_pid,
        )
    identity = _decode_identity(result.get("execution_identity"))
    if status == "error":
        if set(result) != _COMMON_RESULT_FIELDS | _ERROR_RESULT_FIELDS:
            raise ChildProcessFailure(
                "CHILD_RESULT_SCHEMA_INVALID",
                "Child error result is malformed.",
                child_pid,
            )
        code = _bounded_text(result.get("error_code"), "error_code")
        message = _bounded_text(result.get("error_message"), "error_message")
        raise ChildProcessFailure(code, message, child_pid)
    return child_pid, identity


def _invoke(operation: str, request_fields: dict[str, Any]) -> dict[str, Any]:
    request_id = uuid.uuid4().hex
    request = {
        "protocol_version": PROTOCOL_VERSION,
        "request_id": request_id,
        "operation": operation,
        **request_fields,
    }
    with tempfile.TemporaryDirectory(prefix="ev4-stage-qc-operation-") as temporary:
        folder = Path(temporary)
        request_path = folder / "request.json"
        result_path = folder / "result.json"
        _atomic_json_write(request_path, request, MAX_REQUEST_BYTES)
        command = [
            sys.executable,
            "-m",
            CHILD_MODULE,
            "--request",
            str(request_path),
            "--result",
            str(result_path),
        ]
        environment = os.environ.copy()
        source_root = str(Path(__file__).resolve().parents[1])
        current_pythonpath = environment.get("PYTHONPATH")
        environment["PYTHONPATH"] = (
            source_root
            if not current_pythonpath
            else source_root + os.pathsep + current_pythonpath
        )
        environment["PYTHONNOUSERSITE"] = "1"
        try:
            completed = subprocess.run(
                command,
                cwd=folder,
                env=environment,
                capture_output=True,
                text=True,
                timeout=PROCESS_TIMEOUT_SECONDS,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise ChildProcessFailure(
                "CHILD_STARTUP_FAILED",
                f"Fresh child interpreter could not start: {type(exc).__name__}",
            ) from exc
        if completed.returncode != 0:
            raise ChildProcessFailure(
                "CHILD_NONZERO_EXIT",
                (
                    "Fresh child interpreter exited abnormally with code "
                    f"{completed.returncode}."
                ),
            )
        if not result_path.is_file():
            raise ChildProcessFailure(
                "CHILD_RESULT_MISSING",
                "Fresh child interpreter produced no result.",
            )
        result = _load_json(result_path, MAX_RESULT_BYTES)
        child_pid, identity = _validate_common(
            result,
            request_id=request_id,
            operation=operation,
        )
        result["_decoded_child_pid"] = child_pid
        result["_decoded_execution_identity"] = identity
        return result


def _path_text(path: Path) -> str:
    return str(Path(path).expanduser().resolve())


def verify_connection(architect_repository_path: Path) -> ConnectionResult:
    path = Path(architect_repository_path).expanduser().resolve()
    try:
        result = _invoke(
            "verify_connection",
            {"architect_repository_path": str(path)},
        )
        expected = _COMMON_RESULT_FIELDS | _VERIFY_RESULT_FIELDS
        visible = {key for key in result if not key.startswith("_decoded_")}
        if visible != expected:
            raise ChildProcessFailure(
                "CHILD_RESULT_SCHEMA_INVALID",
                "Child verification result is malformed.",
                result["_decoded_child_pid"],
            )
        count = result.get("authority_file_count")
        if not isinstance(count, int) or isinstance(count, bool) or count < 0:
            raise ChildProcessFailure(
                "CHILD_RESULT_SCHEMA_INVALID",
                "Child authority-file count is invalid.",
                result["_decoded_child_pid"],
            )
        connection_ok = result.get("connection_ok")
        if not isinstance(connection_ok, bool):
            raise ChildProcessFailure(
                "CHILD_RESULT_SCHEMA_INVALID",
                "Child connection status is invalid.",
                result["_decoded_child_pid"],
            )
        return ConnectionResult(
            ok=connection_ok,
            path=path,
            commit=_bounded_text(
                result.get("actual_commit"),
                "actual_commit",
                nullable=True,
            ),
            reason=_bounded_text(result.get("connection_reason"), "connection_reason"),
            reference_commit=_bounded_text(
                result.get("reference_commit"),
                "reference_commit",
                nullable=True,
            ),
            runtime_interface_id=_bounded_text(
                result.get("runtime_interface_id"),
                "runtime_interface_id",
                nullable=True,
            ),
            compatibility_mode=_bounded_text(
                result.get("compatibility_mode"),
                "compatibility_mode",
                nullable=True,
            ),
            authority_file_count=count,
            ref=_bounded_text(result.get("ref"), "ref", nullable=True),
            child_pid=result["_decoded_child_pid"],
            execution_identity=result["_decoded_execution_identity"],
        )
    except ChildProcessFailure as exc:
        return ConnectionResult(
            False,
            path,
            None,
            str(exc),
            child_pid=exc.child_pid,
        )


def _validation_result(
    operation: str,
    request_fields: dict[str, Any],
) -> CoreResult:
    try:
        result = _invoke(operation, request_fields)
        expected = _COMMON_RESULT_FIELDS | _VALIDATION_RESULT_FIELDS
        visible = {key for key in result if not key.startswith("_decoded_")}
        if visible != expected:
            raise ChildProcessFailure(
                "CHILD_RESULT_SCHEMA_INVALID",
                "Child validation result is malformed.",
                result["_decoded_child_pid"],
            )
        success = result.get("success")
        if not isinstance(success, bool):
            raise ChildProcessFailure(
                "CHILD_RESULT_SCHEMA_INVALID",
                "Child validation success flag is invalid.",
                result["_decoded_child_pid"],
            )
        attempt_raw = result.get("attempt_path")
        attempt = (
            None
            if attempt_raw is None
            else Path(_bounded_text(attempt_raw, "attempt_path"))
        )
        artifacts_raw = result.get("artifacts")
        if (
            not isinstance(artifacts_raw, list)
            or len(artifacts_raw) > 128
            or any(
                not isinstance(item, str) or not item or len(item) > 1024
                for item in artifacts_raw
            )
        ):
            raise ChildProcessFailure(
                "CHILD_RESULT_SCHEMA_INVALID",
                "Child artifact list is invalid.",
                result["_decoded_child_pid"],
            )
        return CoreResult(
            success,
            attempt,
            _bounded_text(result.get("code"), "code"),
            _bounded_text(result.get("reason"), "reason"),
            _bounded_text(result.get("next_action"), "next_action"),
            tuple(artifacts_raw),
            result["_decoded_child_pid"],
            result["_decoded_execution_identity"],
        )
    except ChildProcessFailure as exc:
        return CoreResult(
            False,
            None,
            exc.code,
            exc.reason,
            "Review the selected paths and retry the operation.",
            child_pid=exc.child_pid,
        )


def run_prefinal_validation(
    stage_output_folder: Path,
    architect_repository_path: Path,
    *,
    source_kind: str = "live_conversation",
) -> CoreResult:
    return _validation_result(
        "run_prefinal_validation",
        {
            "architect_repository_path": _path_text(architect_repository_path),
            "stage_output_folder": _path_text(stage_output_folder),
            "source_kind": source_kind,
        },
    )


def run_final_validation(
    stage_output_folder: Path,
    terminal_stage_output_path: Path,
    architect_repository_path: Path,
    *,
    source_kind: str = "live_conversation",
) -> CoreResult:
    return _validation_result(
        "run_final_validation",
        {
            "architect_repository_path": _path_text(architect_repository_path),
            "stage_output_folder": _path_text(stage_output_folder),
            "terminal_stage_output_path": _path_text(terminal_stage_output_path),
            "source_kind": source_kind,
        },
    )
