"""Strict JSON, distinct digest modes, and atomic writes."""
from __future__ import annotations
import hashlib, json, os, tempfile
from pathlib import Path
class JsonInputError(ValueError): pass
def _pairs(pairs):
 d={}
 for k,v in pairs:
  if k in d: raise JsonInputError(f"duplicate JSON key: {k}")
  d[k]=v
 return d
def _constant(token): raise JsonInputError(f"non-finite JSON number: {token}")
def load_strict(path: Path):
 try: raw=path.read_bytes()
 except OSError as e: raise JsonInputError(f"cannot read {path}: {e}") from e
 if raw.startswith(b'\xef\xbb\xbf'): raise JsonInputError("UTF-8 BOM is not permitted")
 try: value=json.loads(raw.decode('utf-8'), object_pairs_hook=_pairs, parse_constant=_constant)
 except (UnicodeDecodeError,json.JSONDecodeError,JsonInputError) as e: raise JsonInputError(str(e)) from e
 if not isinstance(value,dict): raise JsonInputError("top-level Stage Output must be an object")
 return value
def canonical_bytes(value): return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode('utf-8')
def raw_sha256(path: Path): return hashlib.sha256(path.read_bytes()).hexdigest()
def canonical_sha256(value): return hashlib.sha256(canonical_bytes(value)).hexdigest()
def atomic_write(path: Path, data: bytes):
 path.parent.mkdir(parents=True,exist_ok=True); fd,tmp=tempfile.mkstemp(prefix=f'.{path.name}.',suffix='.tmp',dir=path.parent)
 try:
  with os.fdopen(fd,'wb') as f: f.write(data); f.flush(); os.fsync(f.fileno())
  os.replace(tmp,path)
 finally:
  if os.path.exists(tmp): os.unlink(tmp)
def write_json(path: Path,value): atomic_write(path,canonical_bytes(value))
