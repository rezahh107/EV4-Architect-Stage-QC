from __future__ import annotations
import shutil
from datetime import datetime,timezone
from pathlib import Path
from .architect_adapter import verify
from .json_io import load_strict,raw_sha256,canonical_sha256,write_json,atomic_write
from .models import CoreResult
from .publisher import publish
from .sealed_source import seal
from .wsl_capability import check_wsl
APP_VERSION='0.1.0'
def _utc(): return datetime.now(timezone.utc).isoformat().replace('+00:00','Z')
def _attempt(folder:Path):
 base=folder/'results'; base.mkdir(parents=True,exist_ok=True)
 for n in range(1,1000000):
  p=base/f'attempt-{n:04d}'
  try:p.mkdir(); (p/'input-snapshot').mkdir(); (p/'generated-artifacts').mkdir(); return p
  except FileExistsError:continue
 raise RuntimeError('No attempt directory available')
def _record(attempt, code, reason, action, success=False, artifacts=()):
 write_json(attempt/'diagnostics.json',{'code':code,'reason':reason,'next_action':action})
 atomic_write(attempt/'execution-summary.txt',f'{code}: {reason}\nNext action: {action}\n'.encode())
 write_json(attempt/'attempt-metadata.json',{'schema_version':'1.0','application_version':APP_VERSION,'attempt_id':attempt.name,'started_at_utc':_utc(),'attempt_path':str(attempt)})
 return CoreResult(success,attempt,code,reason,action,tuple(artifacts))
def _discover(folder, manifest, excluded: set[Path] | None = None):
 excluded={item.resolve() for item in (excluded or set())}
 files=sorted((p for p in folder.iterdir() if p.is_file() and p.suffix.casefold()=='.json' and p.resolve() not in excluded),key=lambda p:p.name.casefold())
 outputs=[]; seen={}
 for p in files:
  value=load_strict(p)
  for key in ('stage_id','stage_version','run_id'):
   if not value.get(key): raise ValueError(f'{p.name}: missing required identity field {key}')
  stage=value['stage_id']
  if stage in seen: raise ValueError(f'duplicate Stage Output for {stage}: {seen[stage].name}, {p.name}')
  seen[stage]=p; outputs.append((p,value))
 stages=manifest['project_execution_stages']; pre=stages[:-1]; expected=[x['stage_id'] for x in pre]
 extras=sorted(set(seen)-set(expected))
 if extras: raise ValueError(f'unexpected Stage Output(s): {", ".join(extras)}')
 missing=[x for x in expected if x not in seen]
 if missing: raise ValueError(f'missing mandatory Stage Output(s): {", ".join(missing)}')
 run_ids={v['run_id'] for _,v in outputs}
 if len(run_ids)!=1: raise ValueError('Stage Outputs do not share one run_id')
 ordered=[]
 for stage in pre:
  p=seen[stage['stage_id']]; v=next(v for q,v in outputs if q==p)
  if v['stage_version']!=stage['stage_version']: raise ValueError(f'{p.name}: stage version does not match manifest')
  ordered.append((p,v))
 return ordered
def _snapshot(attempt, entries, terminal=None):
 copied=[]
 for path,_ in entries:
  target=attempt/'input-snapshot'/path.name; shutil.copyfile(path,target); copied.append((target,load_strict(target)))
 if terminal:
  target=attempt/'input-snapshot'/f'terminal-{terminal.name}'; shutil.copyfile(terminal,target); terminal=load_strict(target)
 return copied,terminal
def _context(conn, outputs, run):
 state=run['run_state']; by={v['stage_id']:v for v in outputs}
 return {'context_schema_version':'1.0','authority':{'repository':'rezahh107/EV4-Architect-Repo','commit':conn.commit,'observed_commit_sha':conn.commit,'reference_commit_sha':getattr(conn,'reference_commit',None),'compatibility_mode':getattr(conn,'compatibility_mode',None),'authority_files_verified':len(conn.identities),'ref':getattr(conn,'ref','verified-authority-files'),'raw_file_sha256':conn.identities},'receipt':{'run_status':run['status'],'stages_visited':run['stages_visited'],'semantic_digest':canonical_sha256(run)},'run_state':state,'stage_results':run['results'],'validated_stage_outputs':outputs,'selected_candidate_identity':state.get('selected_candidate_id'),'candidate_lock_state':state.get('selected_candidate_locked'),'build_tree_content':by.get('/build-tree',{}).get('canonical_content'),'implementation_content':by.get('/implementation',{}).get('canonical_content'),'active_and_resolved_unknowns':state.get('unknown_ledger',[]),'final_audit_findings':by.get('/final-audit',{}).get('final_audit_findings',[]),'handoff_export_content':by.get('/handoff-export',{}),'terminal_stage':{'stage_id':'/project-gate-export','stage_version':conn.runtime.load_authority(conn.path)[0]['project_execution_stages'][-1]['stage_version']},'instruction':'Generate exactly one /project-gate-export Stage Output for this Run. The Stage Output must contain the actual project_gate_payload required by the official Architect Runtime and preserve the supplied run identity, selected candidate, validated architecture content, active unknowns, findings, and handoff boundaries. Do not generate or claim an authoritative Stage Result, PASS, next_stage, continuation authorization, caller-authored digest, canonical_payload_valid, legacy_export_substituted, or any other evaluator-owned authority field.' ,'content_identities':{'stage_outputs_canonical_sha256':canonical_sha256(outputs),'run_canonical_sha256':canonical_sha256(run)}}
def run_prefinal_validation(stage_folder:Path, architect_root:Path):
 attempt=_attempt(Path(stage_folder))
 conn=verify(architect_root)
 if not conn.ok:return _record(attempt,'ARCHITECT_CONNECTION_INVALID',conn.reason,'Select a compatible local Architect repository.')
 try:
  manifest,_=conn.runtime.load_authority(conn.path); entries=_discover(Path(stage_folder),manifest); snap,_=_snapshot(attempt,entries); outputs=[v for _,v in snap]
  run=conn.runtime.evaluate_run(outputs,root=conn.path,require_terminal=False)
  if run['status']!='valid':return _record(attempt,'PREFINAL_EVALUATION_FAILED','; '.join(run['errors']),'Repair the reported Stage Output and run again.')
  context=_context(conn,outputs,run); artifacts={'architect-prefinal-qc-receipt.json':context['receipt'],'architect-prefinal-run-state.json':run['run_state'],'architect-prefinal-stage-results.json':run['results'],'architect-final-stage-context.json':context}
  for name,value in artifacts.items():write_json(attempt/'generated-artifacts'/name,value)
  return _record(attempt,'PREFINAL_VALID','Official prefinal evaluation passed.','Give architect-final-stage-context.json to the model.',True,artifacts)
 except Exception as e:return _record(attempt,'PREFINAL_INPUT_INVALID',str(e),'Correct the Stage Output folder and run again.')
def run_final_validation(stage_folder:Path, terminal_path:Path, architect_root:Path):
 attempt=_attempt(Path(stage_folder)); conn=verify(architect_root)
 if not conn.ok:return _record(attempt,'ARCHITECT_CONNECTION_INVALID',conn.reason,'Select a compatible local Architect repository.')
 try:
  manifest,_=conn.runtime.load_authority(conn.path); entries=_discover(Path(stage_folder),manifest,{Path(terminal_path)}); snap,terminal=_snapshot(attempt,entries,Path(terminal_path)); outputs=[v for _,v in snap]
  expected=manifest['project_execution_stages'][-1]
  if terminal.get('run_id')!=outputs[0]['run_id']:raise ValueError('terminal run_id does not match prefinal run')
  if terminal.get('stage_id')!=expected['stage_id'] or terminal.get('stage_version')!=expected['stage_version']:raise ValueError('terminal Stage identity/version does not match manifest')

  if conn.trusted_context is None:return _record(attempt,'ARCHITECT_CONNECTION_INVALID','Verified Architect connection has no trusted producer provenance.','Verify the Architect connection and retry.')
  run=conn.runtime.evaluate_run([*outputs,terminal],root=conn.path,require_terminal=True,trusted_context=conn.trusted_context)
  if run['status']!='valid':return _record(attempt,'FINAL_EVALUATION_FAILED','; '.join(run['errors']),'Repair the terminal Stage Output or upstream evidence and run again.')
  write_json(attempt/'generated-artifacts'/'architect-final-run-state.json',run['run_state']); write_json(attempt/'generated-artifacts'/'architect-final-stage-results.json',run['results'])
  return _record(attempt,'FINAL_VALID','Official terminal evaluation and export validation passed.','Open the final result folder.',True,('architect-final-run-state.json','architect-final-stage-results.json'))
 except Exception as e:return _record(attempt,'FINAL_INPUT_INVALID',str(e),'Correct the input files and run again.')

def run_final_publication(stage_folder:Path, terminal_path:Path, architect_root:Path, publication_root:Path|None=None):
 """Replay final Runtime validation, then publish its payload with the official exporter."""
 attempt=_attempt(Path(stage_folder)); conn=verify(architect_root)
 if not conn.ok:return _record(attempt,'ARCHITECT_CONNECTION_INVALID',conn.reason,'Select a compatible local Architect repository.')
 try:
  sealed=seal(conn.path,conn.commit,attempt/'publication-evidence')
  snapshot_conn=verify(sealed.snapshot)
  if not snapshot_conn.ok:raise RuntimeError(f'Immutable validation snapshot is incompatible: {snapshot_conn.reason}')
  manifest,_=snapshot_conn.runtime.load_authority(snapshot_conn.path); entries=_discover(Path(stage_folder),manifest,{Path(terminal_path)}); snap,terminal=_snapshot(attempt,entries,Path(terminal_path)); outputs=[v for _,v in snap]
  expected=manifest['project_execution_stages'][-1]
  if terminal.get('run_id')!=outputs[0]['run_id']:raise ValueError('terminal run_id does not match prefinal run')
  if terminal.get('stage_id')!=expected['stage_id'] or terminal.get('stage_version')!=expected['stage_version']:raise ValueError('terminal Stage identity/version does not match manifest')
  run=snapshot_conn.runtime.evaluate_run([*outputs,terminal],root=snapshot_conn.path,require_terminal=True,trusted_context=snapshot_conn.trusted_context)
  if run['status']!='valid':return _record(attempt,'FINAL_VALIDATION_FAILED','; '.join(run['errors']),'Repair the terminal Stage Output or upstream evidence and run again.')
  generated=attempt/'generated-artifacts'; write_json(generated/'architect-final-run-state.json',run['run_state']); write_json(generated/'architect-final-stage-results.json',run['results'])
  payload=terminal.get('project_gate_payload')
  if not isinstance(payload,dict):return _record(attempt,'FINAL_PUBLICATION_FAILED','Validated terminal Stage Output does not contain an object project_gate_payload.','Provide a terminal Stage Output with the validated Project Gate payload.')
  if payload.get('run_id') not in (None,terminal['run_id']):return _record(attempt,'FINAL_PUBLICATION_FAILED','Project Gate payload run_id does not match the evaluated Run.','Correct the terminal Stage Output.')
  capability=check_wsl()
  if not capability.ready:return _record(attempt,'FINAL_VALID_PUBLICATION_UNAVAILABLE',f'{capability.code}: {capability.reason}','Final validation passed. Configure WSL Publisher capability, then publish from a new Attempt.',True,('architect-final-run-state.json','architect-final-stage-results.json'))
  result=publish(payload,terminal['run_id'],sealed.snapshot,attempt,publication_root)
  if result.published and result.location:
   write_json(generated/'architect-publication-summary.json',{'status':'FINAL_PUBLISHED_COPY_WARNING' if not result.success else 'FINAL_PUBLISHED','run_id':terminal['run_id'],'architect_repository':'rezahh107/EV4-Architect-Repo','architect_commit_sha':conn.commit,'publisher_branch':result.location.publisher_branch,'publisher_worktree':str(result.location.publisher_worktree),'official_artifact_path':str(result.location.artifact_path),'historical_receipt':result.receipt,'authority_compatibility_mode':conn.compatibility_mode,'authority_files_verified':True})
  if not result.success:
   code='FINAL_COMMITTED_HANDOFF_BLOCKED' if result.published and result.receipt and result.receipt.get('handoff_allowed') is False else ('FINAL_PUBLISHED_COPY_WARNING' if result.published else 'FINAL_PUBLICATION_PRECOMMIT_FAILED')
   action='Official artifact was committed; handoff is blocked. Open the persistent Publisher worktree.' if code=='FINAL_COMMITTED_HANDOFF_BLOCKED' else ('Official artifact was published; open the persistent Publisher worktree.' if result.published else 'Review publication diagnostics and retry with a new Attempt.')
   return _record(attempt,code,result.reason,action,result.published,('architect-final-run-state.json','architect-final-stage-results.json'))
  summary={'status':'FINAL_PUBLISHED','run_id':terminal['run_id'],'architect_repository':'rezahh107/EV4-Architect-Repo','architect_commit_sha':conn.commit,'publisher_branch':result.location.publisher_branch,'publisher_worktree':str(result.location.publisher_worktree),'official_export_exit_code':0,'official_artifact_path':str(result.location.artifact_path.relative_to(result.location.publisher_worktree)),'result_artifact':'architect-project-gate.json','historical_receipt':'architect-project-gate-receipt.json','authority_compatibility_mode':conn.compatibility_mode,'authority_files_verified':True}
  write_json(generated/'architect-publication-summary.json',summary)
  return _record(attempt,'FINAL_PUBLISHED','Official terminal validation and Project Gate publication passed.','Open the result folder.',True,tuple(p.name for p in generated.iterdir()))
 except Exception as e:return _record(attempt,'FINAL_PUBLICATION_FAILED',str(e),'Review publication diagnostics and retry with a new Attempt.')
