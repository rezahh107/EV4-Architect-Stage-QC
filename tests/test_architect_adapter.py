from pathlib import Path
from ev4_architect_stage_qc.architect_adapter import verify
ROOT=Path('/tmp/EV4-Architect-Repo-authority')
def test_locked_live_authority_loads_official_functions():
 c=verify(ROOT,Path('architect-authority.lock.json'))
 assert c.ok and callable(c.runtime.evaluate_run) and callable(c.runtime.evaluate_stage)
def test_wrong_checkout_blocks(tmp_path):
 c=verify(tmp_path,Path('architect-authority.lock.json'));assert not c.ok
