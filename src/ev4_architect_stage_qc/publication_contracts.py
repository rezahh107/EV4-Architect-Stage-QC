"""Strict versioned JSON contracts exchanged by the Windows adapter and POSIX worker."""
from __future__ import annotations
import json, re, uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from .publication_lifecycle import PublicationOutcome, PublicationState

SCHEMA_VERSION = "1.0"; REPOSITORY = "rezahh107/EV4-Architect-Repo"
_SHA = re.compile(r"^[0-9a-f]{64}$"); _COMMIT = re.compile(r"^[0-9a-f]{40}$")
def _object(raw: str) -> dict:
    value = json.loads(raw)
    if not isinstance(value, dict): raise ValueError("contract must be a JSON object")
    return value
def _required(value: dict, names: set[str]) -> None:
    if set(value) != names: raise ValueError("contract object shape is invalid")
def _request_id(value: str) -> str: uuid.UUID(value); return value
@dataclass(frozen=True)
class PublicationRequest:
    request_id: str; run_id: str; architect_commit: str; sealed_bundle_path: str; sealed_bundle_sha256: str; payload_path: str; payload_sha256: str; windows_attempt_path: str
    @classmethod
    def parse(cls, raw: str) -> "PublicationRequest":
        v=_object(raw); _required(v,{"schema_version","request_id","run_id","architect_repository","architect_commit","sealed_bundle_path","sealed_bundle_sha256","payload_path","payload_sha256","windows_attempt_path"})
        if v["schema_version"] != SCHEMA_VERSION or v["architect_repository"] != REPOSITORY: raise ValueError("unsupported request identity")
        _request_id(v["request_id"])
        if not isinstance(v["run_id"],str) or not v["run_id"]: raise ValueError("invalid run_id")
        if not _COMMIT.fullmatch(v["architect_commit"]) or not _SHA.fullmatch(v["sealed_bundle_sha256"]) or not _SHA.fullmatch(v["payload_sha256"]): raise ValueError("invalid digest or commit")
        if not all(isinstance(v[x],str) and v[x] for x in ("sealed_bundle_path","payload_path","windows_attempt_path")): raise ValueError("invalid required path")
        return cls(**{k:v[k] for k in cls.__annotations__})
    def to_json(self) -> str:
        return json.dumps({"schema_version":SCHEMA_VERSION,"architect_repository":REPOSITORY,**asdict(self)}, ensure_ascii=False, separators=(",",":"))
@dataclass(frozen=True)
class PublicationResponse:
    request_id: str; outcome: PublicationOutcome; wsl_distribution: str | None; architect_commit: str
    def to_json(self) -> str:
        o=asdict(self.outcome); o["state"]=self.outcome.state.value
        for k in ("verified_artifact_path","publisher_worktree","recovery_record_path"):
            if o[k] is not None:o[k]=str(o[k])
        return json.dumps({"schema_version":SCHEMA_VERSION,"request_id":self.request_id,"wsl_distribution":self.wsl_distribution,"architect_repository":REPOSITORY,"architect_commit":self.architect_commit,**o},ensure_ascii=False,separators=(",",":"))
