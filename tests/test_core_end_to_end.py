import importlib.util, json, os, sys
from pathlib import Path
import pytest
from ev4_architect_stage_qc.core import run_final_validation,run_prefinal_validation

def authority_root():
 value=os.environ.get('EV4_ARCHITECT_REPO')
 if not value: pytest.skip('integration-only: set EV4_ARCHITECT_REPO to the locked checkout')
 return Path(value)
def official_outputs(root):
 sys.path.insert(0,str(root/'scripts'))
 import architect_quality_runtime  # official module required by its official checker
 spec=importlib.util.spec_from_file_location('official_checker',root/'scripts/check-architect-quality-runtime.py')
 checker=importlib.util.module_from_spec(spec);spec.loader.exec_module(checker)
 return checker.load_outputs(root/'fixtures/architect-quality-runtime/valid/full-pipeline.json',root)
def test_public_core_prefinal_then_final(tmp_path):
 root=authority_root(); outputs=official_outputs(root); stage_dir=tmp_path/'stages';stage_dir.mkdir()
 for index,value in enumerate(outputs[:11],1): (stage_dir/f'{index:02}.json').write_text(json.dumps(value),encoding='utf-8')
 terminal=tmp_path/'terminal.json';terminal.write_text(json.dumps(outputs[11]),encoding='utf-8')
 prefinal=run_prefinal_validation(stage_dir,root)
 assert prefinal.success and prefinal.code=='PREFINAL_VALID'
 generated=prefinal.attempt_path/'generated-artifacts'
 assert {p.name for p in generated.iterdir()} >= {'architect-prefinal-qc-receipt.json','architect-prefinal-run-state.json','architect-prefinal-stage-results.json','architect-final-stage-context.json'}
 context=json.loads((generated/'architect-final-stage-context.json').read_text())
 assert 'project_gate_payload required' in context['instruction'] and 'authoritative Stage Result' in context['instruction']
 assert context['run_state']['run_id']==outputs[0]['run_id'] and context['selected_candidate_identity']==outputs[6]['decision_input']['selected_candidate_id']
 final=run_final_validation(stage_dir,terminal,root)
 assert final.success and final.code=='FINAL_VALID'
 assert {p.name for p in (final.attempt_path/'generated-artifacts').iterdir()} >= {'architect-final-run-state.json','architect-final-stage-results.json'}
def test_terminal_without_payload_fails_official_runtime(tmp_path):
 root=authority_root(); outputs=official_outputs(root); stage_dir=tmp_path/'stages';stage_dir.mkdir()
 for index,value in enumerate(outputs[:11],1): (stage_dir/f'{index:02}.json').write_text(json.dumps(value),encoding='utf-8')
 terminal=tmp_path/'terminal.json'; bad=dict(outputs[11]);bad.pop('project_gate_payload');terminal.write_text(json.dumps(bad),encoding='utf-8')
 result=run_final_validation(stage_dir,terminal,root)
 assert not result.success and result.code=='FINAL_EVALUATION_FAILED'
