import os
from pathlib import Path
import pytest
from ev4_architect_stage_qc.architect_adapter import LOCK_PATH,verify

def authority_root():
 value=os.environ.get('EV4_ARCHITECT_REPO')
 if not value: pytest.skip('integration-only: set EV4_ARCHITECT_REPO to the locked checkout')
 return Path(value)
def test_locked_live_authority_loads_official_functions():
 c=verify(authority_root())
 assert c.ok, c.reason
 assert c.runtime is not None
 assert callable(c.runtime.evaluate_run)
 assert callable(c.runtime.evaluate_stage)
def test_wrong_checkout_blocks(tmp_path):
 c=verify(tmp_path);assert not c.ok
def test_bundled_lock_is_cwd_independent(tmp_path, monkeypatch):
 monkeypatch.chdir(tmp_path)
 assert LOCK_PATH.is_file()
def test_locked_checkout_has_deterministic_provenance():
 c=verify(authority_root())
 assert c.ok, c.reason
 assert c.trusted_context == {'producer_provenance': {'repository':'rezahh107/EV4-Architect-Repo','ref':'locked-exact-commit','commit_sha':'338228cec0aeae951581690c3faba68f512e615c'}}
def test_modified_locked_file_is_rejected(tmp_path):
 import subprocess
 root=authority_root(); worktree=tmp_path/'authority-copy'
 subprocess.run(['git','-C',str(root),'worktree','add','--detach',str(worktree),'HEAD'],check=True,capture_output=True)
 try:
  target=worktree/'contracts/project-gate/producer-gate-export.v1.schema.json'
  target.write_bytes(target.read_bytes()+b'\n ')
  c=verify(worktree)
  assert not c.ok
  assert 'Changed authority file: contracts/project-gate/producer-gate-export.v1.schema.json' == c.reason
 finally:
  subprocess.run(['git','-C',str(root),'worktree','remove','--force',str(worktree)],check=True,capture_output=True)
