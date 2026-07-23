import json
from pathlib import Path
import pytest
from ev4_architect_stage_qc.core import _discover
MANIFEST={'project_execution_stages':[{'stage_id':'/intake','stage_version':'1.0.0'},{'stage_id':'/project-gate-export','stage_version':'1.0.0'}]}
def write(path, stage): path.write_text(json.dumps({'stage_id':stage,'stage_version':'1.0.0','run_id':'r'}),encoding='utf-8')
def test_selected_terminal_inside_prefinal_folder_is_excluded(tmp_path):
 write(tmp_path/'prefinal.json','/intake'); terminal=tmp_path/'terminal.json';write(terminal,'/project-gate-export')
 assert [item[1]['stage_id'] for item in _discover(tmp_path,MANIFEST,{terminal})]==['/intake']
def test_unselected_terminal_still_fails_closed(tmp_path):
 write(tmp_path/'prefinal.json','/intake');write(tmp_path/'terminal.json','/project-gate-export')
 with pytest.raises(ValueError,match='unexpected'): _discover(tmp_path,MANIFEST)
def test_extra_terminal_remains_rejected_when_selected_one_is_excluded(tmp_path):
 write(tmp_path/'prefinal.json','/intake'); selected=tmp_path/'one.json';write(selected,'/project-gate-export');write(tmp_path/'two.json','/project-gate-export')
 with pytest.raises(ValueError,match='unexpected'): _discover(tmp_path,MANIFEST,{selected})
