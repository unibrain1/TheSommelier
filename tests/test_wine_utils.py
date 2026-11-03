"""Tests for the Claude CLI call in wine_utils.py."""

import subprocess

import pytest

import settings
import wine_utils


@pytest.fixture
def clean_env(monkeypatch):
    for name in settings.ALL_VARS:
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def _capture_run(monkeypatch):
    calls = []

    def fake_run(args, **kwargs):
        calls.append(kwargs)
        return subprocess.CompletedProcess(args, 0, stdout=" reply \n", stderr="")

    monkeypatch.setattr(wine_utils.subprocess, "run", fake_run)
    return calls


def test_call_claude_passes_only_the_mode_credential(clean_env):
    clean_env.setenv("CLAUDE_CODE_OAUTH_TOKEN", "SECRET-canary-oauth")
    clean_env.setenv("ANTHROPIC_AUTH_TOKEN", "SECRET-canary-gw")
    clean_env.setenv("PATH", "/usr/bin:/bin")
    calls = _capture_run(clean_env)

    assert wine_utils.call_claude("prompt") == "reply"

    env = calls[0]["env"]
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == "SECRET-canary-oauth"
    assert "ANTHROPIC_AUTH_TOKEN" not in env
    assert "ANTHROPIC_API_KEY" not in env
    assert env["PATH"] == "/usr/bin:/bin"


def test_call_claude_passes_no_credential_when_both_keys_set(clean_env):
    clean_env.setenv("CLAUDE_CODE_OAUTH_TOKEN", "SECRET-canary-oauth")
    clean_env.setenv("ANTHROPIC_API_KEY", "SECRET-canary-api-key")
    calls = _capture_run(clean_env)

    wine_utils.call_claude("prompt")

    assert not set(settings.CLAUDE_CREDENTIALS) & set(calls[0]["env"])


def test_call_claude_without_cli_gives_empty_result(clean_env, capsys):
    def missing(*_args, **_kwargs):
        raise FileNotFoundError("claude-canary")

    clean_env.setattr(wine_utils.subprocess, "run", missing)

    assert wine_utils.call_claude("prompt") == ""
    err = capsys.readouterr().err
    assert "WARNING: Claude CLI could not start" in err
    assert "claude-canary" in err
