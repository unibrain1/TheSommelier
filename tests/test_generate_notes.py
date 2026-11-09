import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import generate_notes
from generate_notes import MAX_NOTE_CHARS, valid_note

SCRIPT = str(Path(__file__).resolve().parent.parent / "scripts" / "generate_notes.py")


def test_valid_note_strips_a_string():
    assert valid_note("  A fresh, bright wine.  ") == "A fresh, bright wine."


def test_valid_note_keeps_a_note_at_the_limit():
    assert valid_note("x" * MAX_NOTE_CHARS) == "x" * MAX_NOTE_CHARS


@pytest.mark.parametrize(
    "note",
    [None, 42, ["a note"], {"note": "a"}, "", "   ", "x" * (MAX_NOTE_CHARS + 1)],
)
def test_valid_note_rejects_a_reply_that_is_not_a_short_string(note):
    assert valid_note(note) is None


def test_generate_notes_writes_only_checked_notes(tmp_path, monkeypatch, capsys):
    plan = tmp_path / "plan.json"
    plan.write_text(
        '{"allWeeks": ['
        '{"week": 1, "year": 1, "name": "Wine A", "vintage": "2020"},'
        '{"week": 2, "year": 1, "name": "Wine B", "vintage": "2019"},'
        '{"week": 3, "year": 1, "name": "Wine C", "vintage": "2018"}]}',
        encoding="utf-8",
    )
    reply = '{"1": "A good note.", "2": 7, "3": "%s"}' % ("y" * (MAX_NOTE_CHARS + 1))
    monkeypatch.setattr(generate_notes, "call_claude", lambda _prompt: reply)

    generate_notes.generate_notes(str(plan))

    weeks = {w["week"]: w for w in json.loads(plan.read_text())["allWeeks"]}
    assert weeks[1]["note"] == "A good note."
    assert not weeks[2].get("note")
    assert not weeks[3].get("note")
    err = capsys.readouterr().err
    assert "week 2" in err
    assert "week 3" in err
    assert "y" * 50 not in err


def _write_plan(path, weeks, note_weeks=()):
    entries = [
        {"week": w, "year": 1, "name": f"Wine {w}", "vintage": "2020"}
        | ({"note": "Already here."} if w in note_weeks else {})
        for w in weeks
    ]
    path.write_text(json.dumps({"allWeeks": entries}), encoding="utf-8")


def test_main_returns_3_and_names_the_missing_weeks(tmp_path, monkeypatch, capsys):
    plan = tmp_path / "plan.json"
    _write_plan(plan, [12, 3, 1, 7, 2])
    reply = '{"1": "Note one.", "2": "Note two."}'
    monkeypatch.setattr(generate_notes, "call_claude", lambda _prompt: reply)

    assert generate_notes.main(["generate_notes.py", str(plan)]) == 3

    err = capsys.readouterr().err
    assert "WARNING: 3 of 5 notes missing: weeks 3, 7, 12" in err
    weeks = {w["week"]: w for w in json.loads(plan.read_text())["allWeeks"]}
    assert weeks[1]["note"] == "Note one."


@pytest.mark.parametrize(
    "reply", ["", "Sorry, I cannot help with that."], ids=["empty", "no-json"]
)
def test_main_returns_1_when_no_notes_are_made(tmp_path, monkeypatch, capsys, reply):
    plan = tmp_path / "plan.json"
    _write_plan(plan, [1, 2, 3], note_weeks=[2])
    before = json.loads(plan.read_text())
    monkeypatch.setattr(generate_notes, "call_claude", lambda _prompt: reply)

    assert generate_notes.main(["generate_notes.py", str(plan)]) == 1

    err = capsys.readouterr().err
    assert "ERROR: No notes generated for any of 2 weeks: 1, 3" in err
    assert json.loads(plan.read_text()) == before


def test_main_returns_0_when_every_note_is_made(tmp_path, monkeypatch, capsys):
    plan = tmp_path / "plan.json"
    _write_plan(plan, [1, 2])
    reply = '{"1": "Note one.", "2": "Note two."}'
    monkeypatch.setattr(generate_notes, "call_claude", lambda _prompt: reply)

    assert generate_notes.main(["generate_notes.py", str(plan)]) == 0

    err = capsys.readouterr().err
    assert "WARNING" not in err
    assert "ERROR" not in err


def test_main_returns_0_when_all_entries_have_notes(tmp_path, monkeypatch, capsys):
    plan = tmp_path / "plan.json"
    _write_plan(plan, [1, 2], note_weeks=[1, 2])

    def no_call(_prompt):
        raise AssertionError("Claude must not be called")

    monkeypatch.setattr(generate_notes, "call_claude", no_call)

    assert generate_notes.main(["generate_notes.py", str(plan)]) == 0
    assert "All entries already have notes." in capsys.readouterr().out


@pytest.mark.parametrize(
    ("reply", "code", "message"),
    [
        ('{"1": "Note one.", "2": "Note two."}', 0, None),
        ('{"1": "Note one."}', 3, "WARNING: 1 of 2 notes missing: weeks 2"),
        ("", 1, "ERROR: No notes generated for any of 2 weeks: 1, 2"),
    ],
    ids=["complete", "partial", "none"],
)
def test_script_exit_code(tmp_path, reply, code, message):
    # A fake claude CLI on PATH, so the real process runs with no network.
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    reply_file = tmp_path / "reply.txt"
    reply_file.write_text(reply, encoding="utf-8")
    fake = bin_dir / "claude"
    fake.write_text(f'#!/bin/sh\ncat "{reply_file}"\n', encoding="utf-8")
    fake.chmod(0o755)
    plan = tmp_path / "plan.json"
    _write_plan(plan, [1, 2])
    env = {"PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"}

    result = subprocess.run(
        [sys.executable, SCRIPT, str(plan)],
        capture_output=True,
        text=True,
        env=env,
    )

    assert result.returncode == code, result.stderr
    if message:
        assert message in result.stderr
