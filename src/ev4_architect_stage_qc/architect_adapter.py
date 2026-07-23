from __future__ import annotations
import importlib.util
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from .json_io import raw_sha256
LOCK_PATH = Path(__file__).resolve().parents[2] / 'architect-authority.lock.json'
@dataclass(frozen=True)
class Connection:
 ok: bool; path: Path; commit: str|None; reason: str; identities: dict[str,str]; runtime: object|None=None; trusted_context: dict|None=None
def sibling_checkout():
 p=Path.cwd().parent/'EV4-Architect-Repo'; return p if p.is_dir() else None
def _git(root, *args):
 try:return subprocess.check_output(['git','-C',str(root),*args],text=True,stderr=subprocess.DEVNULL).strip()
 except Exception:return None
def _identity(root):
 remote=_git(root,'config','--get','remote.origin.url') or ''
 normalized=remote.removesuffix('.git').replace('git@github.com:','https://github.com/').casefold()
 return 'rezahh107/ev4-architect-repo' if normalized.endswith('/rezahh107/ev4-architect-repo') else None
def verify(root: Path, lock_path: Path|None=None):
 root=Path(root).expanduser().resolve(); identities={}; lock_path=lock_path or LOCK_PATH
 try: lock=json.loads(lock_path.read_text('utf-8'))
 except Exception:return Connection(False,root,_git(root,'rev-parse','HEAD'),'QC authority compatibility lock is unavailable.',identities)
 commit=_git(root,'rev-parse','HEAD')
 if not commit:return Connection(False,root,None,'Selected Architect folder is not a Git checkout.',identities)
 if _identity(root)!=lock.get('repository','').casefold():return Connection(False,root,commit,'Selected checkout identity is not rezahh107/EV4-Architect-Repo.',identities)
 if commit != lock.get('commit_sha'):return Connection(False,root,commit,f'Architect commit mismatch: expected {lock.get("commit_sha")}, found {commit}.',identities)
 for rel,want in lock['files'].items():
  file=root/rel
  if not file.is_file(): return Connection(False,root,commit,f'Required authority file missing: {rel}',identities)
  got=raw_sha256(file); identities[rel]=got
  if got != want:return Connection(False,root,commit,f'Changed authority file: {rel}. Update EV4 Architect Stage QC before continuing.',identities)
 spec=importlib.util.spec_from_file_location('ev4_official_runtime',root/'scripts/architect_quality_runtime.py')
 if not spec or not spec.loader:return Connection(False,root,commit,'Official evaluator cannot be loaded.',identities)
 try:
  module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module); module.load_authority(root)
 except Exception as exc:return Connection(False,root,commit,f'Official authority compatibility failed: {type(exc).__name__}: {exc}',identities)
 if not callable(getattr(module,'evaluate_run',None)) or not callable(getattr(module,'evaluate_stage',None)):
  return Connection(False,root,commit,'Official evaluator entry points are missing.',identities)
 trusted_context={'producer_provenance': {'repository': lock['repository'], 'ref': 'locked-exact-commit', 'commit_sha': commit}}
 return Connection(True,root,commit,'Compatible Architect runtime.',identities,module,trusted_context)
