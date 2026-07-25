from __future__ import annotations

import os

import pytest


@pytest.fixture(scope="session", autouse=True)
def deterministic_git_text_materialization():
    """Keep test-created Git worktrees byte-stable without changing user config."""
    keys = {
        "GIT_CONFIG_COUNT": "2",
        "GIT_CONFIG_KEY_0": "core.autocrlf",
        "GIT_CONFIG_VALUE_0": "false",
        "GIT_CONFIG_KEY_1": "core.eol",
        "GIT_CONFIG_VALUE_1": "lf",
    }
    previous = {key: os.environ.get(key) for key in keys}
    os.environ.update(keys)
    try:
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
