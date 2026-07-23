import pytest
from ev4_architect_stage_qc.json_io import JsonInputError,canonical_bytes,canonical_sha256,load_strict,raw_sha256,write_json

def test_strict_json_and_digests(tmp_path):
 p=tmp_path/'a.json'; p.write_text('{"b":1,"a":{"x":2}}',encoding='utf-8')
 assert load_strict(p)['a']['x']==2
 assert canonical_bytes({'b':1,'a':2})==b'{"a":2,"b":1}'
 assert canonical_sha256({'a':2,'b':1})==canonical_sha256({'b':1,'a':2})
 assert raw_sha256(p)!=canonical_sha256(load_strict(p))
@pytest.mark.parametrize('text',["{'x':1}",'{"x":NaN}','{"x":Infinity}','{"x":1,"x":2}','{"a":{"x":1,"x":2}}','[]'])
def test_rejects_ambiguous_json(tmp_path,text):
 p=tmp_path/'a.json';p.write_text(text,encoding='utf-8')
 with pytest.raises(JsonInputError):load_strict(p)
def test_atomic_canonical_write(tmp_path):
 p=tmp_path/'out.json';write_json(p,{'z':'é','a':1});assert p.read_bytes()==b'{"a":1,"z":"\xc3\xa9"}'
@pytest.mark.parametrize('text',["{\"value\":1e999999}","{\"value\":-1e999999}","{\"nested\":{\"value\":1e999999}}",'{"items":[1e999999]}'])
def test_rejects_overflowed_json_floats(tmp_path,text):
 p=tmp_path/'overflow.json';p.write_text(text,encoding='utf-8')
 with pytest.raises(JsonInputError,match='non-finite JSON number'):load_strict(p)
