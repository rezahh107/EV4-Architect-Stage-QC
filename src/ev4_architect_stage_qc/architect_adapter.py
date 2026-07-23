from __future__ import annotations
import hashlib
import importlib.util
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
LOCK_PATH = Path(__file__).resolve().parents[2] / 'architect-authority.lock.json'
@dataclass(frozen=True)
class Connection:
 ok: bool; path: Path; commit: str|None; reason: str; identities: dict[str,str]; runtime: object|None=None; trusted_context: dict|None=None
def sibling_checkout():
 p=Path.cwd().parent/'EV4-Architect-Repo'; return p if p.is_dir() else None
def _git_text(root, *args):
 try:return subprocess.check_output(['git','-C',str(root),*args],text=True,stderr=subprocess.DEVNULL).strip()
 except Exception:return None
def _git_bytes(root, *args):
 try:return subprocess.check_output(['git','-C',str(root),*args],stderr=subprocess.DEVNULL)
 except Exception:return None
def _identity(root):
 remote=_git_text(root,'config','--get','remote.origin.url') or ''
 normalized=remote.removesuffix('.git').replace('git@github.com:','https://github.com/').casefold()
 return 'rezahh107/ev4-architect-repo' if normalized.endswith('/rezahh107/ev4-architect-repo') else None
def _failed(root, commit, reason, identities): return Connection(False,root,commit,reason,identities)
def verify(root: Path, lock_path: Path|None=None):
 root=Path(root).expanduser().resolve(); identities={}; lock_path=lock_path or LOCK_PATH
 try: lock=json.loads(lock_path.read_text('utf-8'))
 except Exception:return _failed(root,_git_text(root,'rev-parse','HEAD'),'QC authority compatibility lock is unavailable.',identities)
 commit=_git_text(root,'rev-parse','HEAD')
 if not commit:return _failed(root,None,'Selected Architect folder is not a Git checkout.',identities)
 if _identity(root)!=lock.get('repository','').casefold():return _failed(root,commit,'Selected checkout identity is not rezahh107/EV4-Architect-Repo.',identities)
 if commit != lock.get('commit_sha'):return _failed(root,commit,f'Architect commit mismatch: expected {lock.get("commit_sha")}, found {commit}.',identities)
 for rel,want in lock['files'].items():
  if not (root/rel).is_file(): return _failed(root,commit,f'Required authority working-tree file missing: {rel}',identities)
  committed_oid=_git_text(root,'rev-parse',f'{commit}:{rel}')
  if not committed_oid:return _failed(root,commit,f'Required authority file missing from locked commit: {rel}',identities)
  working_oid=_git_text(root,'hash-object',f'--path={rel}',rel)
  if working_oid != committed_oid:return _failed(root,commit,f'Changed authority file: {rel}',identities)
  blob=_git_bytes(root,'cat-file','blob',committed_oid)
  if blob is None:return _failed(root,commit,f'Locked authority blob cannot be read: {rel}',identities)
  got=hashlib.sha256(blob).hexdigest(); identities[rel]=got
  if got != want:return _failed(root,commit,f'Authority lock mismatch for committed blob: {rel}',identities)
 spec=importlib.util.spec_from_file_location('ev4_official_runtime',root/'scripts/architect_quality_runtime.py')
 if not spec or not spec.loader:return _failed(root,commit,'Official evaluator cannot be loaded.',identities)
 try:
  module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module); module.load_authority(root)
 except Exception as exc:return _failed(root,commit,f'Official authority compatibility failed: {type(exc).__name__}: {exc}',identities)
 if not callable(getattr(module,'evaluate_run',None)) or not callable(getattr(module,'evaluate_stage',None)):
  return _failed(root,commit,'Official evaluator entry points are missing.',identities)
 trusted_context={'producer_provenance': {'repository': lock['repository'], 'ref': 'locked-exact-commit', 'commit_sha': commit}}
 return Connection(True,root,commit,'Compatible Architect runtime.',identities,module,trusted_context)
