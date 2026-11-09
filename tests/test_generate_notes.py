import json

import pytest

import generate_notes
from generate_notes import MAX_NOTE_CHARS, valid_note


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
