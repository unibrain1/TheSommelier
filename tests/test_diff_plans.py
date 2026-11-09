"""Unit tests for diff_plans() in scripts/generate_plan.py.

diff_plans() compares two allWeeks lists and returns changelog entries using
pure set-difference logic:
  - Old weeks not in new  → "removed" entries
  - New weeks not in old  → "added" entries
  - Common weeks          → no entry (no "adjusted" type)

The "adjusted" change type is retired. This test suite covers the retired
behavior as regression anchors as well as the new set-difference behavior.
"""

from generate_plan import diff_plans


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def week(vintage: str, name: str, week_number: int) -> dict:
    """Minimal week dict — only the three keys diff_plans() cares about."""
    return {"vintage": vintage, "name": name, "week": week_number}


def _chateau(wk: int) -> dict:
    """Convenience: a single Chateau Example bottle scheduled at week *wk*."""
    return week("2019", "Chateau Example", wk)


def _reserve(wk: int) -> dict:
    """Convenience: a second distinct wine for multi-wine isolation tests."""
    return week("2021", "Reserve Blanc", wk)


def _third(wk: int) -> dict:
    """Convenience: a third distinct wine."""
    return week("2017", "Domaine Rouge", wk)


# ---------------------------------------------------------------------------
# TestDiffPlansFullRemoval — N → 0 (regression: existing behavior)
# ---------------------------------------------------------------------------


class TestDiffPlansFullRemoval:
    """Wine present in old plan, absent from new plan entirely."""

    def test_single_bottle_removed(self):
        # Arrange
        old = [_chateau(5)]
        new: list[dict] = []

        # Act
        changes = diff_plans(old, new)

        # Assert
        assert len(changes) == 1
        assert changes[0]["type"] == "removed"
        assert changes[0]["week"] == 5

    def test_multiple_bottles_of_same_wine_all_removed(self):
        # Arrange: two bottles scheduled in different weeks
        old = [_chateau(5), _chateau(20)]
        new: list[dict] = []

        # Act
        changes = diff_plans(old, new)

        # Assert: one "removed" entry per week
        assert len(changes) == 2
        types = {c["type"] for c in changes}
        assert types == {"removed"}
        weeks = {c["week"] for c in changes}
        assert weeks == {5, 20}

    def test_three_bottles_all_removed(self):
        # Arrange
        old = [_chateau(3), _chateau(15), _chateau(40)]
        new: list[dict] = []

        # Act
        changes = diff_plans(old, new)

        # Assert
        assert len(changes) == 3
        assert all(c["type"] == "removed" for c in changes)
        assert {c["week"] for c in changes} == {3, 15, 40}


# ---------------------------------------------------------------------------
# TestDiffPlansFullAddition — 0 → N (regression: existing behavior)
# ---------------------------------------------------------------------------


class TestDiffPlansFullAddition:
    """Wine absent from old plan, present in new plan."""

    def test_single_bottle_added(self):
        # Arrange
        old: list[dict] = []
        new = [_chateau(10)]

        # Act
        changes = diff_plans(old, new)

        # Assert
        assert len(changes) == 1
        assert changes[0]["type"] == "added"
        assert changes[0]["week"] == 10

    def test_multiple_bottles_of_same_wine_all_added(self):
        # Arrange
        old: list[dict] = []
        new = [_chateau(10), _chateau(30)]

        # Act
        changes = diff_plans(old, new)

        # Assert
        assert len(changes) == 2
        types = {c["type"] for c in changes}
        assert types == {"added"}
        weeks = {c["week"] for c in changes}
        assert weeks == {10, 30}

    def test_three_bottles_all_added(self):
        # Arrange
        old: list[dict] = []
        new = [_chateau(2), _chateau(18), _chateau(45)]

        # Act
        changes = diff_plans(old, new)

        # Assert
        assert len(changes) == 3
        assert all(c["type"] == "added" for c in changes)
        assert {c["week"] for c in changes} == {2, 18, 45}


# ---------------------------------------------------------------------------
# TestDiffPlansNoChange — no entries expected
# ---------------------------------------------------------------------------


class TestDiffPlansNoChange:
    """Identical old and new plans produce no changelog entries."""

    def test_empty_plans(self):
        # Arrange / Act / Assert
        assert diff_plans([], []) == []

    def test_same_wine_same_week(self):
        # Arrange
        old = [_chateau(5)]
        new = [_chateau(5)]

        # Act
        changes = diff_plans(old, new)

        # Assert
        assert changes == []

    def test_multiple_bottles_of_same_wine_same_weeks(self):
        # Arrange: two bottles, identical scheduling in both plans
        old = [_chateau(5), _chateau(20)]
        new = [_chateau(5), _chateau(20)]

        # Act
        changes = diff_plans(old, new)

        # Assert
        assert changes == []

    def test_two_distinct_wines_both_unchanged(self):
        # Arrange
        old = [_chateau(5), _reserve(12)]
        new = [_chateau(5), _reserve(12)]

        # Act
        changes = diff_plans(old, new)

        # Assert
        assert changes == []


# ---------------------------------------------------------------------------
# TestDiffPlansPartialRemoval — THE BUG CASE
#
# Wine had N bottles in the old plan; the new plan has fewer (M < N).
# The surviving week(s) must produce no entry; the dropped week(s) must each
# produce exactly one "removed" entry.  No "adjusted" entries may appear.
# ---------------------------------------------------------------------------


class TestDiffPlansPartialRemoval:
    """One or more bottles removed while at least one week survives unchanged."""

    def test_two_to_one_surviving_week_unchanged(self):
        # Arrange: old has weeks 5 and 20; new keeps only week 5
        old = [_chateau(5), _chateau(20)]
        new = [_chateau(5)]

        # Act
        changes = diff_plans(old, new)

        # Assert: exactly one "removed" for week 20; week 5 is silent
        assert len(changes) == 1
        assert changes[0]["type"] == "removed"
        assert changes[0]["week"] == 20

    def test_three_to_one_two_weeks_removed(self):
        # Arrange
        old = [_chateau(3), _chateau(15), _chateau(40)]
        new = [_chateau(15)]

        # Act
        changes = diff_plans(old, new)

        # Assert: two "removed" entries (weeks 3 and 40), zero for week 15
        assert len(changes) == 2
        assert all(c["type"] == "removed" for c in changes)
        assert {c["week"] for c in changes} == {3, 40}

    def test_two_to_one_where_surviving_slot_is_different(self):
        # Arrange: old [3, 41] → new [5]
        # No week survives; all old are removed; new week is added
        old = [_chateau(3), _chateau(41)]
        new = [_chateau(5)]

        # Act
        changes = diff_plans(old, new)

        # Assert: removed 3, removed 41, added 5
        assert len(changes) == 3
        removed_weeks = {c["week"] for c in changes if c["type"] == "removed"}
        added_weeks = {c["week"] for c in changes if c["type"] == "added"}
        assert removed_weeks == {3, 41}
        assert added_weeks == {5}

    def test_no_adjusted_type_ever_emitted(self):
        # Regression guard: "adjusted" must never appear regardless of scenario
        old = [_chateau(5), _chateau(20)]
        new = [_chateau(5), _chateau(22)]  # one week shifted

        changes = diff_plans(old, new)

        types = {c["type"] for c in changes}
        assert "adjusted" not in types


# ---------------------------------------------------------------------------
# TestDiffPlansPartialAddition — symmetric to partial removal
# ---------------------------------------------------------------------------


class TestDiffPlansPartialAddition:
    """One or more bottles added while the original week survives unchanged."""

    def test_one_to_two_original_week_unchanged(self):
        # Arrange: old has week 10; new adds week 30
        old = [_chateau(10)]
        new = [_chateau(10), _chateau(30)]

        # Act
        changes = diff_plans(old, new)

        # Assert: exactly one "added" for week 30; week 10 is silent
        assert len(changes) == 1
        assert changes[0]["type"] == "added"
        assert changes[0]["week"] == 30

    def test_one_to_three_two_weeks_added(self):
        # Arrange
        old = [_chateau(10)]
        new = [_chateau(10), _chateau(25), _chateau(45)]

        # Act
        changes = diff_plans(old, new)

        # Assert: two "added" entries (weeks 25 and 45), week 10 silent
        assert len(changes) == 2
        assert all(c["type"] == "added" for c in changes)
        assert {c["week"] for c in changes} == {25, 45}


# ---------------------------------------------------------------------------
# TestDiffPlansSameCountDifferentWeeks — pure reschedule
#
# All weeks changed (no common week survives).  Old plan semantics
# (prev version used "adjusted") → new semantics: one "removed" + one "added"
# per shifted slot.
# ---------------------------------------------------------------------------


class TestDiffPlansSameCountDifferentWeeks:
    """Same bottle count, every week has moved — was 'adjusted', now removed+added."""

    def test_single_bottle_moved(self):
        # Arrange: old [5] → new [10]
        old = [_chateau(5)]
        new = [_chateau(10)]

        # Act
        changes = diff_plans(old, new)

        # Assert: one "removed" at 5, one "added" at 10
        assert len(changes) == 2
        removed = [c for c in changes if c["type"] == "removed"]
        added = [c for c in changes if c["type"] == "added"]
        assert len(removed) == 1 and removed[0]["week"] == 5
        assert len(added) == 1 and added[0]["week"] == 10

    def test_two_bottles_both_moved(self):
        # Arrange: old [5, 20] → new [8, 22]
        old = [_chateau(5), _chateau(20)]
        new = [_chateau(8), _chateau(22)]

        # Act
        changes = diff_plans(old, new)

        # Assert: removed 5 + 20, added 8 + 22
        assert len(changes) == 4
        removed_weeks = {c["week"] for c in changes if c["type"] == "removed"}
        added_weeks = {c["week"] for c in changes if c["type"] == "added"}
        assert removed_weeks == {5, 20}
        assert added_weeks == {8, 22}

    def test_no_adjusted_type_on_pure_reschedule(self):
        # Explicit regression: the old "adjusted" type must not be produced
        old = [_chateau(5)]
        new = [_chateau(10)]

        changes = diff_plans(old, new)

        assert all(c["type"] != "adjusted" for c in changes)


# ---------------------------------------------------------------------------
# TestDiffPlansMultipleWines — isolation between distinct wines
# ---------------------------------------------------------------------------


class TestDiffPlansMultipleWines:
    """Changes to one wine must not bleed into entries for another wine."""

    def test_wine_a_removed_wine_b_added_wine_c_unchanged(self):
        # Arrange
        #   Chateau Example (A): old=[5],     new=[]      → removed
        #   Reserve Blanc   (B): old=[],      new=[12]    → added
        #   Domaine Rouge   (C): old=[30],    new=[30]    → silent
        old = [_chateau(5), _third(30)]
        new = [_reserve(12), _third(30)]

        # Act
        changes = diff_plans(old, new)

        # Assert: exactly two entries; wine C generates nothing
        assert len(changes) == 2

        removed = [c for c in changes if c["type"] == "removed"]
        added = [c for c in changes if c["type"] == "added"]

        assert len(removed) == 1
        assert removed[0]["week"] == 5
        assert "Chateau Example" in removed[0]["description"]

        assert len(added) == 1
        assert added[0]["week"] == 12
        assert "Reserve Blanc" in added[0]["description"]

    def test_three_wines_all_changed_independently(self):
        # Arrange: each wine shifts its week independently
        old = [_chateau(5), _reserve(10), _third(20)]
        new = [_chateau(8), _reserve(13), _third(25)]

        # Act
        changes = diff_plans(old, new)

        # Assert: 3 removed + 3 added = 6 entries, no wine bleeds into another
        assert len(changes) == 6
        removed_weeks = {c["week"] for c in changes if c["type"] == "removed"}
        added_weeks = {c["week"] for c in changes if c["type"] == "added"}
        assert removed_weeks == {5, 10, 20}
        assert added_weeks == {8, 13, 25}

    def test_wine_with_no_changes_contributes_zero_entries(self):
        # Arrange: only _reserve changes; _chateau stays put
        old = [_chateau(5), _reserve(10)]
        new = [_chateau(5), _reserve(15)]

        # Act
        changes = diff_plans(old, new)

        # Assert: only entries relate to Reserve Blanc
        assert len(changes) == 2
        for c in changes:
            assert "Reserve Blanc" in c["description"]
            assert "Chateau Example" not in c["description"]


# ---------------------------------------------------------------------------
# TestDiffPlansOutputShape — contract / schema tests
# ---------------------------------------------------------------------------


class TestDiffPlansOutputShape:
    """Every change entry must conform to the expected schema."""

    def _collect_all_changes(self) -> list[dict]:
        """Run several scenarios and pool all returned entries."""
        scenarios = [
            diff_plans([_chateau(5)], []),
            diff_plans([], [_chateau(10)]),
            diff_plans([_chateau(5)], [_chateau(10)]),
            diff_plans([_chateau(5), _chateau(20)], [_chateau(5)]),
            diff_plans([_chateau(5)], [_chateau(5), _chateau(20)]),
            diff_plans([_chateau(3), _reserve(10)], [_reserve(10), _third(25)]),
        ]
        return [entry for result in scenarios for entry in result]

    def test_every_entry_has_required_keys(self):
        for entry in self._collect_all_changes():
            assert "type" in entry, f"Missing 'type' key: {entry}"
            assert "week" in entry, f"Missing 'week' key: {entry}"
            assert "description" in entry, f"Missing 'description' key: {entry}"

    def test_type_values_are_only_added_or_removed(self):
        for entry in self._collect_all_changes():
            assert entry["type"] in ("added", "removed"), (
                f"Unexpected type value '{entry['type']}': {entry}"
            )

    def test_week_values_are_integers(self):
        for entry in self._collect_all_changes():
            assert isinstance(entry["week"], int), f"'week' is not an int: {entry}"

    def test_description_contains_wine_label(self):
        # Label is constructed as "{vintage} {name}"; both must appear
        old = [week("2019", "Chateau Example", 5)]
        new: list[dict] = []

        changes = diff_plans(old, new)

        assert len(changes) == 1
        assert "2019" in changes[0]["description"]
        assert "Chateau Example" in changes[0]["description"]

    def test_description_for_added_entry_contains_label(self):
        old: list[dict] = []
        new = [week("2021", "Reserve Blanc", 10)]

        changes = diff_plans(old, new)

        assert len(changes) == 1
        assert "2021" in changes[0]["description"]
        assert "Reserve Blanc" in changes[0]["description"]

    def test_no_extra_keys_beyond_contract(self):
        # The function must not silently add unexpected fields
        EXPECTED_KEYS = {"type", "week", "description"}
        for entry in self._collect_all_changes():
            extra = set(entry.keys()) - EXPECTED_KEYS
            assert not extra, f"Unexpected keys {extra} in entry: {entry}"

    def test_empty_inputs_return_empty_list(self):
        result = diff_plans([], [])
        assert result == []
        assert isinstance(result, list)

    def test_return_type_is_list(self):
        result = diff_plans([_chateau(5)], [_chateau(10)])
        assert isinstance(result, list)
