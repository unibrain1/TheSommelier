"""Unit tests for reuse_notes() and usable_weeks() in scripts/generate_plan.py.

reuse_notes() copies a valid note from the previous plan to a new week when
the vintage, name, urgent, evolution and occasion fields match. Each old note
goes to one new week. usable_weeks() and usable_history() reject the parts of
a live plan with a bad shape.
"""

import pytest

from generate_notes import MAX_NOTE_CHARS
from generate_plan import reuse_notes, usable_history, usable_weeks


def week(
    week_number: int,
    name: str = "Chateau Example",
    vintage: str | int = "2019",
    urgent: bool = False,
    evolution: bool = False,
    occasion: str | None = None,
    note: object = "",
) -> dict:
    """Minimal week dict — only the keys reuse_notes() reads or writes."""
    return {
        "week": week_number,
        "vintage": vintage,
        "name": name,
        "urgent": urgent,
        "evolution": evolution,
        "occasion": occasion,
        "note": note,
    }


def test_same_plan_reuses_every_note():
    old = [week(1, "A", note="Note A."), week(2, "B", note="Note B.")]
    new = [week(1, "A"), week(2, "B")]

    assert reuse_notes(old, new) == 2
    assert [w["note"] for w in new] == ["Note A.", "Note B."]


def test_wine_that_moves_to_another_week_keeps_its_note():
    old = [week(3, note="Moved note.")]
    new = [week(7)]

    assert reuse_notes(old, new) == 1
    assert new[0]["note"] == "Moved note."


def test_different_wine_gets_no_note():
    old = [week(1, "A", note="Note A.")]
    new = [week(1, "B")]

    assert reuse_notes(old, new) == 0
    assert new[0]["note"] == ""


@pytest.mark.parametrize(
    ("old_fields", "new_fields"),
    [
        ({"vintage": "2019"}, {"vintage": "2020"}),
        ({"urgent": False}, {"urgent": True}),
        ({"urgent": True}, {"urgent": False}),
        ({"evolution": False}, {"evolution": True}),
        ({"evolution": True}, {"evolution": False}),
        ({"occasion": None}, {"occasion": "Thanksgiving"}),
        ({"occasion": "Thanksgiving"}, {"occasion": None}),
        ({"occasion": "Thanksgiving"}, {"occasion": "Christmas"}),
    ],
)
def test_changed_field_gets_a_new_note(old_fields, new_fields):
    old = [week(1, note="Old note.", **old_fields)]
    new = [week(1, **new_fields)]

    assert reuse_notes(old, new) == 0
    assert new[0]["note"] == ""


@pytest.mark.parametrize("old_weeks", [None, []])
def test_no_previous_plan_reuses_nothing(old_weeks):
    new = [week(1)]

    assert reuse_notes(old_weeks, new) == 0
    assert new[0]["note"] == ""


@pytest.mark.parametrize(
    "bad_note", ["", "   ", None, 42, ["a note"], "x" * (MAX_NOTE_CHARS + 1)]
)
def test_note_that_is_not_valid_is_not_reused(bad_note):
    old = [week(1, note=bad_note)]
    new = [week(1)]

    assert reuse_notes(old, new) == 0
    assert new[0]["note"] == ""


def test_reused_note_is_stripped():
    old = [week(1, note="  Padded note.  ")]
    new = [week(1)]

    reuse_notes(old, new)

    assert new[0]["note"] == "Padded note."


def test_notes_of_one_wine_go_out_in_week_order():
    # Old weeks out of order: the note of week 2 comes first.
    old = [week(9, note="Second."), week(2, note="First.")]
    new = [week(4), week(10), week(20)]

    assert reuse_notes(old, new) == 2
    assert [w["note"] for w in new] == ["First.", "Second.", ""]


def test_empty_and_none_occasion_match():
    old = [week(1, occasion="", note="No occasion.")]
    new = [week(1, occasion=None)]

    assert reuse_notes(old, new) == 1


def test_int_and_str_vintage_match():
    old = [week(1, vintage=2019, note="Int vintage.")]
    new = [week(1, vintage="2019")]

    assert reuse_notes(old, new) == 1
    assert new[0]["note"] == "Int vintage."


def test_week_without_fields_does_not_crash():
    old = [{"note": "Bare note."}]
    new = [{"note": ""}]

    assert reuse_notes(old, new) == 1
    assert new[0]["note"] == "Bare note."


def test_usable_weeks_returns_a_good_list():
    weeks = [week(1), week(2, occasion="Thanksgiving")]

    assert usable_weeks(weeks) is weeks


@pytest.mark.parametrize(
    "value",
    [
        None,
        {"1": week(1)},
        "weeks",
        52,
        [week(1), "not a week"],
        [{**week(1), "week": "1"}],
        [{**week(1), "week": None}],
        [{**week(1), "week": True}],
        [{"note": "No week field."}],
        [{**week(1), "occasion": ["Thanksgiving"]}],
        [{**week(1), "occasion": {"name": "Thanksgiving"}}],
    ],
)
def test_usable_weeks_rejects_a_bad_shape(value):
    assert usable_weeks(value) is None


def test_usable_history_keeps_entries_with_a_usable_shape():
    good = {"date": "2026-11-09", "changes": [{"type": "added", "week": 1}]}
    bad = [
        {"date": 20261109, "changes": []},
        {"date": "2026-11-09", "changes": 5},
        {"date": "2026-11-09", "changes": [None]},
        {"date": "2026-11-09"},
        {"changes": []},
        "entry",
        None,
    ]

    assert usable_history([good, *bad]) == [good]


@pytest.mark.parametrize("value", [None, 5, "history", {"date": "2026-11-09"}])
def test_usable_history_of_a_value_that_is_not_a_list_is_empty(value):
    assert usable_history(value) == []
