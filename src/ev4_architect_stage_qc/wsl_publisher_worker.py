"""One-shot POSIX publication worker.  It is deliberately not a service."""
from __future__ import annotations
import argparse, hashlib, os, subprocess, tempfile
from pathlib import Path
from .architect_adapter import verify
from .json_io import atomic_write, canonical_bytes, load_strict, write_json
from .publication_contracts import PublicationRequest, PublicationResponse
from .publication_lifecycle import classify_publication
from .publisher import create_unique_publisher_worktree, _parse_object

def _sha(path: Path) -> str: return hashlib.sha256(path.read_bytes()).hexdigest()
def execute(request: PublicationRequest, response_path: Path, distribution: str | None = None) -> PublicationResponse:
    bundle,payload=Path(request.sealed_bundle_path),Path(request.payload_path)
    if _sha(bundle)!=request.sealed_bundle_sha256 or _sha(payload)!=request.payload_sha256: raise RuntimeError("sealed bundle or payload SHA-256 mismatch")
    root=Path.home()/".local/share/EV4ArchitectStageQC"
    (root/"repositories").mkdir(parents=True,exist_ok=True); (root/"responses").mkdir(parents=True,exist_ok=True)
    runtime=Path(tempfile.mkdtemp(prefix=request.request_id+"-",dir=root/"repositories")); source=runtime/"architect"
    subprocess.run(["git","clone",str(bundle),str(source)],check=True,capture_output=True,text=True)
    subprocess.run(["git","-C",str(source),"checkout","--detach",request.architect_commit],check=True,capture_output=True,text=True)
    if subprocess.run(["git","-C",str(source),"rev-parse","--is-shallow-repository"],capture_output=True,text=True).stdout.strip()=="true": raise RuntimeError("sealed source is shallow")
    subprocess.run(["git","-C",str(source),"fsck","--no-dangling"],check=True,capture_output=True,text=True)
    connection=verify(source)
    if not connection.ok or connection.commit!=request.architect_commit: raise RuntimeError(f"authority or exact commit verification failed: {connection.reason}")
    loc=create_unique_publisher_worktree(source,root/"publisher-worktrees")
    rel=Path(".ev4-stage-qc")/"publications"/request.request_id; target=loc.publisher_worktree/rel/"validated-architect-stage-payload.json"; atomic_write(target,payload.read_bytes())
    output=loc.publisher_worktree/rel/"architect-project-gate.json"
    p=subprocess.run(["python3","scripts/export-architect-project-gate.py","--repo-root",str(loc.publisher_worktree),"--payload",str(rel/target.name),"--run-id",request.run_id,"--output",str(rel/output.name),"--format","json"],cwd=loc.publisher_worktree,capture_output=True,text=True,check=False)
    receipt=_parse_object(p.stdout,"historical receipt") if p.stdout.strip() else None
    update=None; warnings=[]
    if p.stderr.strip():
        try:update=_parse_object(p.stderr,"receipt update")
        except ValueError as exc:warnings.append(f"Receipt Update parsing failed: {exc}")
    exists=output.is_file(); valid=True
    if exists:
        try: load_strict(output)
        except Exception: valid=False
    outcome=classify_publication(receipt,update,p.returncode,exists,tuple(warnings),artifact_valid=valid)
    outcome=outcome.__class__(**{**outcome.__dict__,"publisher_branch":loc.publisher_branch,"publisher_worktree":loc.publisher_worktree,"committed_output_location":str(output),"verified_artifact_path":output if exists else None})
    recovery=root/"responses"/f"{request.request_id}.json"; response=PublicationResponse(request.request_id,outcome,distribution,request.architect_commit)
    atomic_write(recovery,response.to_json().encode()); outcome=outcome.__class__(**{**outcome.__dict__,"recovery_record_path":recovery}); response=PublicationResponse(request.request_id,outcome,distribution,request.architect_commit); atomic_write(response_path,response.to_json().encode())
    return response
def main() -> int:
    ap=argparse.ArgumentParser(); ap.add_argument("--request",required=True); ap.add_argument("--response",required=True); ap.add_argument("--distribution"); args=ap.parse_args()
    execute(PublicationRequest.parse(Path(args.request).read_text("utf-8")),Path(args.response),args.distribution); return 0
if __name__ == "__main__": raise SystemExit(main())
