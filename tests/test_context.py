from types import SimpleNamespace
from ev4_architect_stage_qc.core import _context
class Runtime:
 def load_authority(self, root): return ({'project_execution_stages':[{'stage_version':'1.0.0'}]}, {})
def test_final_context_requires_payload_and_forbids_authority_claims():
 run={'status':'valid','stages_visited':['/handoff-export'],'run_state':{'unknown_ledger':[]},'results':[]}
 context=_context(SimpleNamespace(commit='a'*40,identities={},runtime=Runtime(),path='.'),[{'stage_id':'/handoff-export'}],run)
 instruction=context['instruction']
 assert 'project_gate_payload required' in instruction
 assert 'canonical_payload_valid' in instruction and 'legacy_export_substituted' in instruction
 assert 'Do not generate or claim' in instruction
 assert 'Do not generate or claim an authoritative Stage Result' in instruction
