from __future__ import annotations
import os
from pathlib import Path
from .json_io import load_strict,write_json
def settings_path(): return Path(os.environ.get('LOCALAPPDATA',Path.home()/'AppData'/'Local'))/'EV4ArchitectStageQC'/'settings.json'
def load_settings():
 try:
  value=load_strict(settings_path()); return value if value.get('schema_version')=='1.0' else {}
 except Exception:return {}
def save_architect_path(path): write_json(settings_path(),{'schema_version':'1.0','architect_repository_path':str(Path(path).resolve())})
