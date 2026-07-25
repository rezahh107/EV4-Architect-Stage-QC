from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path, PurePosixPath
from typing import Any, Sequence

LOCK_SCHEMA_VERSION = "4.0"
EXPECTED_REPOSITORY = "rezahh107/EV4-Architect-Repo"
EXPECTED_INTERFACE = "ev4-architect-quality-runtime@2.0.0"
MANIFEST_PATH = "manifests/architect-runtime-authority-manifest.v1.json"
WRAPPER_PATH = "scripts/architect_quality_runtime.py"
FINALIZATION_SYMBOL = "finalize_project_gate"
SHA1_RE = re.compile(r"^[0-9a-f]{40}$")
ENTRY_POINTS = tuple(
    sorted(
        (
            f"{WRAPPER_PATH}#ProjectGateFinalizationResult",
            f"{WRAPPER_PATH}#RunContext",
            f"{WRAPPER_PATH}#evaluate_run",
            f"{WRAPPER_PATH}#evaluate_stage",
            f"{WRAPPER_PATH}#finalize_project_gate",
        )
    )
)


class LockGenerationError(ValueError):
    """Raised when the selected Architect checkout cannot produce a canonical Lock."""


def _git_text(root: Path, *args: str) -> str:
    try:
        return subprocess.check_output(
            ["git", "-C", str(root), *args],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        raise LockGenerationError(f"Git command failed: {' '.join(args)}") from exc


def _git_bytes(root: Path, *args: str) -> bytes:
    try:
        return subprocess.check_output(
            ["git", "-C", str(root), *args],
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise LockGenerationError(f"Git command failed: {' '.join(args)}") from exc


def _normalize_repository(remote: str) -> str | None:
    value = remote.strip().removesuffix("/").removesuffix(".git")
    if value.startswith("git@github.com:"):
        value = "https://github.com/" + value.removeprefix("git@github.com:")
    elif value.startswith("ssh://git@github.com/"):
        value = "https://github.com/" + value.removeprefix("ssh://git@github.com/")
    value = value.casefold()
    expected = f"https://github.com/{EXPECTED_REPOSITORY}".casefold()
    return EXPECTED_REPOSITORY if value == expected else None


def _canonical_relative_path(value: Any) -> str:
    if not isinstance(value, str) or not value:
        raise LockGenerationError("Manifest authority path must be a non-empty string.")
    if "\\" in value:
        raise LockGenerationError(f"Manifest authority path is noncanonical: {value}")
    candidate = PurePosixPath(value)
    if (
        candidate.is_absolute()
        or str(candidate) != value
        or not candidate.parts
        or any(part in {"", ".", ".."} for part in candidate.parts)
        or ":" in candidate.parts[0]
    ):
        raise LockGenerationError(f"Manifest authority path is noncanonical: {value}")
    return value


def _safe_working_path(root: Path, relative: str) -> Path:
    root = root.resolve()
    cursor = root
    for part in PurePosixPath(relative).parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise LockGenerationError(f"Authority path traverses a symlink: {relative}")
    try:
        resolved = cursor.resolve(strict=True)
    except OSError as exc:
        raise LockGenerationError(f"Authority path is missing: {relative}") from exc
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise LockGenerationError(f"Authority path escapes the repository: {relative}") from exc
    if not resolved.is_file():
        raise LockGenerationError(f"Authority path is not a regular file: {relative}")
    return resolved


def _committed_blob(root: Path, commit: str, relative: str) -> tuple[str, bytes]:
    try:
        oid = _git_text(root, "rev-parse", f"{commit}:{relative}")
    except LockGenerationError as exc:
        raise LockGenerationError(
            f"Authority path is missing from selected commit: {relative}"
        ) from exc
    if not SHA1_RE.fullmatch(oid):
        raise LockGenerationError(f"Authority blob OID is malformed: {relative}")
    if _git_text(root, "cat-file", "-t", oid) != "blob":
        raise LockGenerationError(f"Authority path is not a committed blob: {relative}")
    return oid, _git_bytes(root, "cat-file", "blob", oid)


def _read_verified_committed_file(root: Path, commit: str, relative: str) -> tuple[str, bytes]:
    path = _safe_working_path(root, relative)
    oid, committed = _committed_blob(root, commit, relative)
    try:
        working = path.read_bytes()
    except OSError as exc:
        raise LockGenerationError(f"Authority path cannot be read: {relative}") from exc
    if working != committed:
        raise LockGenerationError(
            f"Authority working-tree bytes differ from committed blob: {relative}"
        )
    return oid, committed


def _manifest_inventory(manifest: Any) -> list[str]:
    if not isinstance(manifest, dict):
        raise LockGenerationError("Runtime Authority Manifest must be a JSON object.")
    if manifest.get("owner_repository") != EXPECTED_REPOSITORY:
        raise LockGenerationError("Runtime Authority Manifest repository identity mismatch.")
    if manifest.get("runtime_interface_id") != EXPECTED_INTERFACE:
        raise LockGenerationError("Runtime Authority Manifest interface identity mismatch.")

    public = manifest.get("public_entry_points")
    if not isinstance(public, list):
        raise LockGenerationError("Runtime Authority Manifest public entry points are malformed.")
    finalizer_declared = any(
        isinstance(item, dict)
        and item.get("path") == WRAPPER_PATH
        and isinstance(item.get("symbols"), list)
        and FINALIZATION_SYMBOL in item["symbols"]
        for item in public
    )
    if not finalizer_declared:
        raise LockGenerationError("Runtime Authority Manifest omits the public finalization entrypoint.")

    paths: list[str] = []
    seen: set[str] = set()
    for key in ("python_authority_paths", "data_authority_paths"):
        values = manifest.get(key)
        if not isinstance(values, list):
            raise LockGenerationError(f"Runtime Authority Manifest inventory is malformed: {key}")
        for raw in values:
            relative = _canonical_relative_path(raw)
            if relative in seen:
                raise LockGenerationError(
                    f"Runtime Authority Manifest contains duplicate authority path: {relative}"
                )
            seen.add(relative)
            paths.append(relative)
    manifest_relative = _canonical_relative_path(MANIFEST_PATH)
    if manifest_relative in seen:
        raise LockGenerationError(
            f"Runtime Authority Manifest contains duplicate authority path: {manifest_relative}"
        )
    paths.append(manifest_relative)
    return sorted(paths)


def read_reference_commit(lock_path: Path) -> str:
    try:
        value = json.loads(Path(lock_path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise LockGenerationError("Committed Architect authority Lock is unreadable.") from exc
    if not isinstance(value, dict):
        raise LockGenerationError("Committed Architect authority Lock must be a JSON object.")
    commit = value.get("reference_commit_sha")
    if not isinstance(commit, str) or not SHA1_RE.fullmatch(commit):
        raise LockGenerationError("Lock reference_commit_sha must be 40 lowercase hexadecimal characters.")
    return commit


def generate_lock_document(
    architect_root: Path,
    *,
    expected_commit: str | None = None,
) -> dict[str, Any]:
    root = Path(architect_root).expanduser().resolve()
    if not (root / ".git").exists():
        raise LockGenerationError("Selected Architect folder is not a Git checkout.")

    commit = _git_text(root, "rev-parse", "HEAD")
    if not SHA1_RE.fullmatch(commit):
        raise LockGenerationError("Selected Architect checkout commit is malformed.")
    if expected_commit is not None:
        if not SHA1_RE.fullmatch(expected_commit):
            raise LockGenerationError(
                "Expected Architect commit must be 40 lowercase hexadecimal characters."
            )
        if commit != expected_commit:
            raise LockGenerationError("Selected Architect checkout does not match expected commit.")

    remote = _git_text(root, "config", "--get", "remote.origin.url")
    if _normalize_repository(remote) != EXPECTED_REPOSITORY:
        raise LockGenerationError("Selected checkout identity is not rezahh107/EV4-Architect-Repo.")

    _, manifest_bytes = _read_verified_committed_file(root, commit, MANIFEST_PATH)
    try:
        manifest = json.loads(manifest_bytes.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise LockGenerationError("Runtime Authority Manifest is not valid UTF-8 JSON.") from exc
    inventory = _manifest_inventory(manifest)

    files: dict[str, str] = {}
    for relative in inventory:
        oid, _ = _read_verified_committed_file(root, commit, relative)
        files[relative] = oid

    return {
        "compatibility_mode": "authority_file_identity",
        "entry_points": list(ENTRY_POINTS),
        "files": dict(sorted(files.items())),
        "identity_algorithm": "git_blob_oid_sha1",
        "reference_commit_sha": commit,
        "repository": EXPECTED_REPOSITORY,
        "runtime_interface_id": EXPECTED_INTERFACE,
        "schema_version": LOCK_SCHEMA_VERSION,
    }


def canonical_lock_bytes(document: dict[str, Any]) -> bytes:
    return (
        json.dumps(
            document,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def generate_lock_bytes(
    architect_root: Path,
    *,
    expected_commit: str | None = None,
) -> bytes:
    return canonical_lock_bytes(
        generate_lock_document(architect_root, expected_commit=expected_commit)
    )


def check_lock(
    architect_root: Path,
    lock_path: Path,
    *,
    expected_commit: str | None = None,
) -> bool:
    expected = generate_lock_bytes(architect_root, expected_commit=expected_commit)
    try:
        actual = Path(lock_path).read_bytes()
    except OSError:
        return False
    return actual == expected


def write_lock(
    architect_root: Path,
    lock_path: Path,
    *,
    expected_commit: str | None = None,
) -> bytes:
    output = generate_lock_bytes(architect_root, expected_commit=expected_commit)
    destination = Path(lock_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.tmp-{os.getpid()}")
    try:
        temporary.write_bytes(output)
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    return output


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate or verify the canonical Architect authority Lock.")
    parser.add_argument("--architect", type=Path)
    parser.add_argument("--lock", type=Path, required=True)
    parser.add_argument("--expected-commit")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true")
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--print-reference", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.print_reference:
            print(read_reference_commit(args.lock))
            return 0
        if args.architect is None:
            raise LockGenerationError("--architect is required for --write and --check.")
        if args.write:
            write_lock(
                args.architect,
                args.lock,
                expected_commit=args.expected_commit,
            )
            return 0
        if not check_lock(
            args.architect,
            args.lock,
            expected_commit=args.expected_commit,
        ):
            print("Committed Architect authority Lock is stale or noncanonical.", file=sys.stderr)
            return 1
        return 0
    except LockGenerationError as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
