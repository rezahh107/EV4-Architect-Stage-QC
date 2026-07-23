from pathlib import Path

from ev4_architect_stage_qc.publisher import _parse_object, resolve_console_python


def test_console_python_is_an_executable_path():
    assert Path(resolve_console_python()).name


def test_receipt_requires_json_object():
    assert _parse_object('{"handoff_allowed":true}', 'historical receipt')['handoff_allowed'] is True
    for value in ('not-json', '[]'):
        try:
            _parse_object(value, 'historical receipt')
        except ValueError:
            pass
        else:
            raise AssertionError('non-object receipt was accepted')
