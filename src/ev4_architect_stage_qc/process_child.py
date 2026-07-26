from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Sequence

PROTOCOL_VERSION = "1.0"
MAX_REQUEST_BYTES = 64 * 1024
MAX_RESULT_BYTES = 1024 * 1024
_COMMON_REQUEST_FIELDS = {
    "protocol_version",
    "request_id",
    "operation",
    "architect_repository_path",
}
_OPERATION_FIELDS = {
    "verify_connection": set(),
    "run_prefix_validation": {"stage_output_folder", "source_kind"},
    "run_prefinal_validation": {"stage_output_folder", "source_kind"},
    "run_final_validation": {
        "stage_output_folder",
        "terminal_stage_output_path",
        "source_kind",
    },
}
_IDENTITY_FIELDS = (
    "wrapper_origin",
    "package_origin",
    "history_origin",
    "finalization_origin",
)


class ProtocolError(ValueError):
    pass


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ProtocolError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _load_request(path: Path) -> dict[str, Any]:
    data = path.read_bytes()
    if len(data) > MAX_REQUEST_BYTES:
        raise ProtocolError("request exceeds the bounded size limit")
    try:
        value = json.loads(data.decode("utf-8"), object_pairs_hook=_strict_object)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ProtocolError("request is not strict UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise ProtocolError("request must be a JSON object")
    return value


def _text(value: Any, field: str, *, maximum: int = 32768) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > maximum
        or "\x00" in value
    ):
        raise ProtocolError(f"invalid request field: {field}")
    return value


def _validate_request(value: dict[str, Any]) -> dict[str, Any]:
    if value.get("protocol_version") != PROTOCOL_VERSION:
        raise ProtocolError("unsupported protocol_version")
    request_id = _text(value.get("request_id"), "request_id", maximum=128)
    operation = _text(value.get("operation"), "operation", maximum=128)
    if operation not in _OPERATION_FIELDS:
        raise ProtocolError("unknown operation")
    expected = _COMMON_REQUEST_FIELDS | _OPERATION_FIELDS[operation]
    if set(value) != expected:
        raise ProtocolError("request fields do not match the bounded operation contract")
    _text(value.get("architect_repository_path"), "architect_repository_path")
    for field in _OPERATION_FIELDS[operation]:
        maximum = 128 if field == "source_kind" else 32768
        _text(value.get(field), field, maximum=maximum)
    return dict(value, request_id=request_id, operation=operation)


def _atomic_write(path: Path, value: dict[str, Any]) -> None:
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
    if len(data) > MAX_RESULT_BYTES:
        raise ProtocolError("result exceeds the bounded size limit")
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    try:
        temporary.write_bytes(data)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _empty_identity() -> dict[str, None]:
    return {field: None for field in _IDENTITY_FIELDS}


def _execution_identity(root: Path) -> dict[str, str | None]:
    modules = {
        "wrapper_origin": sys.modules.get("ev4_official_runtime"),
        "package_origin": sys.modules.get("architect_quality_runtime"),
        "history_origin": sys.modules.get("architect_quality_runtime.history"),
        "finalization_origin": sys.modules.get("architect_project_gate_finalization"),
    }
    if all(module is None for module in modules.values()):
        return _empty_identity()
    if any(module is None for module in modules.values()):
        raise RuntimeError("Architect Runtime module origin set is incomplete.")
    expected = {
        "wrapper_origin": root / "scripts/architect_quality_runtime.py",
        "package_origin": root / "scripts/architect_quality_runtime/__init__.py",
        "history_origin": root / "scripts/architect_quality_runtime/history.py",
        "finalization_origin": root / "scripts/architect_project_gate_finalization.py",
    }
    root = root.resolve()
    result: dict[str, str | None] = {}
    for field, module in modules.items():
        raw = getattr(module, "__file__", None)
        if not isinstance(raw, str) or not raw:
            raise RuntimeError(
                f"Architect Runtime module origin is unavailable: {field}"
            )
        observed = Path(raw).resolve(strict=True)
        try:
            observed.relative_to(root)
        except ValueError as exc:
            raise RuntimeError(
                f"Architect Runtime module origin escapes selected checkout: {field}"
            ) from exc
        if observed != expected[field].resolve(strict=True):
            raise RuntimeError(
                f"Architect Runtime module origin does not match selected checkout: {field}"
            )
        result[field] = str(observed)
    return result


def _common(
    request: dict[str, Any],
    status: str,
    identity: dict[str, Any],
) -> dict[str, Any]:
    return {
        "protocol_version": PROTOCOL_VERSION,
        "request_id": request["request_id"],
        "operation": request["operation"],
        "child_pid": os.getpid(),
        "status": status,
        "execution_identity": identity,
    }


def _verify(request: dict[str, Any]) -> dict[str, Any]:
    from .architect_adapter import verify

    root = Path(request["architect_repository_path"])
    connection = verify(root)
    identity = (
        _execution_identity(root)
        if connection.runtime is not None
        else _empty_identity()
    )
    return {
        **_common(request, "completed", identity),
        "connection_ok": connection.ok,
        "connection_reason": connection.reason,
        "actual_commit": connection.commit,
        "reference_commit": connection.reference_commit,
        "runtime_interface_id": connection.runtime_interface_id,
        "compatibility_mode": connection.compatibility_mode,
        "authority_file_count": len(connection.identities),
        "ref": connection.ref,
    }


def _validation_payload(request: dict[str, Any], result: Any) -> dict[str, Any]:
    root = Path(request["architect_repository_path"])
    identity = _execution_identity(root)
    return {
        **_common(request, "completed", identity),
        "success": bool(result.success),
        "attempt_path": (
            None if result.attempt_path is None else str(result.attempt_path)
        ),
        "code": str(result.code),
        "reason": str(result.reason),
        "next_action": str(result.next_action),
        "artifacts": [str(item) for item in result.artifacts],
    }


def _prefinal(request: dict[str, Any]) -> dict[str, Any]:
    from .core import run_prefinal_validation

    result = run_prefinal_validation(
        Path(request["stage_output_folder"]),
        Path(request["architect_repository_path"]),
        source_kind=request["source_kind"],
    )
    return _validation_payload(request, result)


def _prefix(request: dict[str, Any]) -> dict[str, Any]:
    from .core import run_prefix_validation

    result = run_prefix_validation(
        Path(request["stage_output_folder"]),
        Path(request["architect_repository_path"]),
        source_kind=request["source_kind"],
    )
    return _validation_payload(request, result)


def _final(request: dict[str, Any]) -> dict[str, Any]:
    from .core import run_final_validation

    result = run_final_validation(
        Path(request["stage_output_folder"]),
        Path(request["terminal_stage_output_path"]),
        Path(request["architect_repository_path"]),
        source_kind=request["source_kind"],
    )
    return _validation_payload(request, result)


def _execute(request: dict[str, Any]) -> dict[str, Any]:
    operation = request["operation"]
    if operation == "verify_connection":
        return _verify(request)
    if operation == "run_prefix_validation":
        return _prefix(request)
    if operation == "run_prefinal_validation":
        return _prefinal(request)
    if operation == "run_final_validation":
        return _final(request)
    raise ProtocolError("unknown operation")


def _error_result(raw: dict[str, Any], exc: Exception) -> dict[str, Any]:
    request_id = raw.get("request_id")
    operation = raw.get("operation")
    if not isinstance(request_id, str):
        request_id = ""
    if not isinstance(operation, str):
        operation = ""
    request = {"request_id": request_id, "operation": operation}
    return {
        **_common(request, "error", _empty_identity()),
        "error_code": (
            "MALFORMED_REQUEST"
            if isinstance(exc, ProtocolError)
            else "CHILD_OPERATION_EXCEPTION"
        ),
        "error_message": f"{type(exc).__name__}: {str(exc)[:2048]}",
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run exactly one Stage-QC operation."
    )
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--result", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    raw: dict[str, Any] = {}
    try:
        raw = _load_request(args.request)
        request = _validate_request(raw)
        result = _execute(request)
    except Exception as exc:
        result = _error_result(raw, exc)
    try:
        _atomic_write(args.result, result)
    except Exception:
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
