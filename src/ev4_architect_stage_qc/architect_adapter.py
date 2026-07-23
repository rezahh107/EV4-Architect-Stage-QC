from __future__ import annotations
import importlib.util,json, subprocess
from dataclasses import dataclass
from pathlib import Path
from .json_io import raw_sha256
@dataclass(frozen=True)
class Connection:
 ok: bool; path: Path; commit: str|None; reason: str; identities: dict[str,str]; runtime: object|None=None
def sibling_checkout():
 p=Path.cwd().parent/'EV4-Architect-Repo'; return p if p.is_dir() else None
def _commit(root):
 try:return subprocess.check_output(['git','-C',str(root),'rev-parse','HEAD'],text=True,stderr=subprocess.DEVNULL).strip()
 except Exception:return None
def verify(root: Path, lock_path: Path=Path('architect-authority.lock.json')):
 root=Path(root).expanduser().resolve(); identities={}
 if not (root/'manifests/architect-pipeline-manifest.v1.json').is_file(): return Connection(False,root,_commit(root),'Expected Architect pipeline manifest was not found.',identities)
 try: lock=json.loads(lock_path.read_text('utf-8'))
 except Exception:return Connection(False,root,_commit(root),'QC authority compatibility lock is unavailable.',identities)
 for rel,want in lock['files'].items():
  file=root/rel
  if not file.is_file(): return Connection(False,root,_commit(root),f'Required authority file missing: {rel}',identities)
  got=raw_sha256(file); identities[rel]=got
  if got != want:return Connection(False,root,_commit(root),f'Changed authority file: {rel}. Update EV4 Architect Stage QC before continuing.',identities)
 spec=importlib.util.spec_from_file_location('ev4_official_runtime',root/'scripts/architect_quality_runtime.py')
 if not spec or not spec.loader:return Connection(False,root,_commit(root),'Official evaluator cannot be loaded.',identities)
 module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
 if not callable(getattr(module,'evaluate_run',None)) or not callable(getattr(module,'evaluate_stage',None)):
  return Connection(False,root,_commit(root),'Official evaluator entry points are missing.',identities)
 return Connection(True,root,_commit(root),'Compatible Architect runtime.',identities,module)
