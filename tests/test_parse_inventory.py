"""Unit tests for parse_inventory() in scripts/parse_inventory.py.

Tests cover:
- KEEP_FIELDS contains the new fields added in issue #27
- PRO_SCORE_FIELDS constant matches expected values
- Backward compatibility: minimal TSV with original fields only
- Type coercions for new fields (pro scores, cNotes, Price, Valuation)
- TotalValuation aggregation across bottles in the same group
- TotalValuation when all Valuations are missing
- Pro scores are None when absent from the TSV
- PurchaseDate preserved as a string

The conftest.py at project root adds scripts/ to sys.path.
"""

from pathlib import Path

import pytest

from parse_inventory import KEEP_FIELDS, PRO_SCORE_FIELDS, parse_inventory
from tests.helpers import ALL_FIELDS, ORIGINAL_FIELDS, build_inventory_tsv, write_tsv
from tests.helpers import minimal_row as _minimal_row

# ---------------------------------------------------------------------------
# TSV helpers
# ---------------------------------------------------------------------------


def _write_tsv(tmp_path: Path, headers: list[str], rows: list[dict]) -> Path:
    """Write a TSV file with the given headers and row dicts, return its Path."""
    return write_tsv(tmp_path / "test_inventory.tsv", headers, rows)


# ---------------------------------------------------------------------------
# TestKeepFieldsExpansion
# ---------------------------------------------------------------------------


class TestKeepFieldsExpansion:
    """KEEP_FIELDS should include all new fields added in issue #27."""

    def test_wa_in_keep_fields(self):
        assert "WA" in KEEP_FIELDS

    def test_ws_in_keep_fields(self):
        assert "WS" in KEEP_FIELDS

    def test_bh_in_keep_fields(self):
        assert "BH" in KEEP_FIELDS

    def test_ag_in_keep_fields(self):
        assert "AG" in KEEP_FIELDS

    def test_jr_in_keep_fields(self):
        assert "JR" in KEEP_FIELDS

    def test_js_in_keep_fields(self):
        assert "JS" in KEEP_FIELDS

    def test_jg_in_keep_fields(self):
        assert "JG" in KEEP_FIELDS

    def test_cnotes_in_keep_fields(self):
        assert "cNotes" in KEEP_FIELDS

    def test_price_in_keep_fields(self):
        assert "Price" in KEEP_FIELDS

    def test_purchase_date_in_keep_fields(self):
        assert "PurchaseDate" in KEEP_FIELDS

    def test_store_name_in_keep_fields(self):
        assert "StoreName" in KEEP_FIELDS

    def test_valuation_in_keep_fields(self):
        assert "Valuation" in KEEP_FIELDS

    def test_original_fields_still_present(self):
        # Ensure none of the original fields were accidentally removed
        for field in ORIGINAL_FIELDS:
            assert field in KEEP_FIELDS, (
                f"Original field '{field}' missing from KEEP_FIELDS"
            )


# ---------------------------------------------------------------------------
# TestProScoreFields
# ---------------------------------------------------------------------------


class TestProScoreFields:
    """PRO_SCORE_FIELDS constant must exactly match the expected critic abbreviations."""

    EXPECTED = ("WA", "WS", "BH", "AG", "JR", "JS", "JG")

    def test_pro_score_fields_is_tuple(self):
        assert isinstance(PRO_SCORE_FIELDS, tuple)

    def test_pro_score_fields_length(self):
        assert len(PRO_SCORE_FIELDS) == len(self.EXPECTED)

    def test_pro_score_fields_contents(self):
        assert set(PRO_SCORE_FIELDS) == set(self.EXPECTED)

    def test_pro_score_fields_exact_order(self):
        assert PRO_SCORE_FIELDS == self.EXPECTED


# ---------------------------------------------------------------------------
# TestBackwardCompatibility
# ---------------------------------------------------------------------------


class TestBackwardCompatibility:
    """Parsing a TSV with only the original 22 fields should not break.

    New fields should be None (numeric coercions on empty string) or empty
    string (string fields that were absent from the TSV).
    """

    def test_parse_succeeds_with_original_fields_only(self, tmp_path):
        row = _minimal_row()
        tsv = _write_tsv(tmp_path, ORIGINAL_FIELDS, [row])
        wines = parse_inventory(tsv)
        assert len(wines) == 1

    def test_original_fields_unchanged(self, tmp_path):
        row = _minimal_row()
        tsv = _write_tsv(tmp_path, ORIGINAL_FIELDS, [row])
        w = parse_inventory(tsv)[0]
        assert w["Wine"] == "Chateau Test"
        assert w["Vintage"] == 2018
        assert w["BeginConsume"] == 2022
        assert w["EndConsume"] == 2030
        assert w["CT"] == pytest.approx(92.0)
        assert w["Quantity"] == 1

    def test_new_pro_score_fields_are_none_when_absent(self, tmp_path):
        # TSV has no WA/WS/etc columns — all should parse to None
        row = _minimal_row()
        tsv = _write_tsv(tmp_path, ORIGINAL_FIELDS, [row])
        w = parse_inventory(tsv)[0]
        for field in PRO_SCORE_FIELDS:
            assert w[field] is None, f"Expected {field} to be None when absent from TSV"

    def test_cnotes_is_none_when_absent(self, tmp_path):
        row = _minimal_row()
        tsv = _write_tsv(tmp_path, ORIGINAL_FIELDS, [row])
        w = parse_inventory(tsv)[0]
        assert w["cNotes"] is None

    def test_price_is_none_when_absent(self, tmp_path):
        row = _minimal_row()
        tsv = _write_tsv(tmp_path, ORIGINAL_FIELDS, [row])
        w = parse_inventory(tsv)[0]
        assert w["Price"] is None

    def test_valuation_is_none_when_absent(self, tmp_path):
        row = _minimal_row()
        tsv = _write_tsv(tmp_path, ORIGINAL_FIELDS, [row])
        w = parse_inventory(tsv)[0]
        assert w["Valuation"] is None

    def test_total_valuation_is_none_when_absent(self, tmp_path):
        row = _minimal_row()
        tsv = _write_tsv(tmp_path, ORIGINAL_FIELDS, [row])
        w = parse_inventory(tsv)[0]
        assert w["TotalValuation"] is None


# ---------------------------------------------------------------------------
# TestNewFieldTypeCoercion
# ---------------------------------------------------------------------------


class TestNewFieldTypeCoercion:
    """New fields must be coerced to their correct Python types after parsing."""

    def _make_tsv(self, tmp_path, overrides: dict | None = None) -> Path:
        row = _minimal_row(overrides)
        return _write_tsv(tmp_path, ALL_FIELDS, [row])

    def test_wa_coerced_to_float(self, tmp_path):
        tsv = self._make_tsv(tmp_path, {"WA": "95"})
        w = parse_inventory(tsv)[0]
        assert isinstance(w["WA"], float)
        assert w["WA"] == pytest.approx(95.0)

    def test_ws_coerced_to_float(self, tmp_path):
        tsv = self._make_tsv(tmp_path, {"WS": "90"})
        w = parse_inventory(tsv)[0]
        assert isinstance(w["WS"], float)
        assert w["WS"] == pytest.approx(90.0)

    def test_bh_coerced_to_float(self, tmp_path):
        tsv = self._make_tsv(tmp_path, {"BH": "88"})
        w = parse_inventory(tsv)[0]
        assert isinstance(w["BH"], float)
        assert w["BH"] == pytest.approx(88.0)

    def test_ag_coerced_to_float(self, tmp_path):
        tsv = self._make_tsv(tmp_path, {"AG": "93.5"})
        w = parse_inventory(tsv)[0]
        assert isinstance(w["AG"], float)
        assert w["AG"] == pytest.approx(93.5)

    def test_jr_coerced_to_float(self, tmp_path):
        tsv = self._make_tsv(tmp_path, {"JR": "91"})
        w = parse_inventory(tsv)[0]
        assert isinstance(w["JR"], float)
        assert w["JR"] == pytest.approx(91.0)

    def test_js_coerced_to_float(self, tmp_path):
        tsv = self._make_tsv(tmp_path, {"JS": "96"})
        w = parse_inventory(tsv)[0]
        assert isinstance(w["JS"], float)
        assert w["JS"] == pytest.approx(96.0)

    def test_jg_coerced_to_float(self, tmp_path):
        tsv = self._make_tsv(tmp_path, {"JG": "89"})
        w = parse_inventory(tsv)[0]
        assert isinstance(w["JG"], float)
        assert w["JG"] == pytest.approx(89.0)

    def test_cnotes_coerced_to_int(self, tmp_path):
        tsv = self._make_tsv(tmp_path, {"cNotes": "3"})
        w = parse_inventory(tsv)[0]
        assert isinstance(w["cNotes"], int)
        assert w["cNotes"] == 3

    def test_price_coerced_to_float(self, tmp_path):
        tsv = self._make_tsv(tmp_path, {"Price": "49.99"})
        w = parse_inventory(tsv)[0]
        assert isinstance(w["Price"], float)
        assert w["Price"] == pytest.approx(49.99)

    def test_valuation_coerced_to_float(self, tmp_path):
        tsv = self._make_tsv(tmp_path, {"Valuation": "65.00"})
        w = parse_inventory(tsv)[0]
        assert isinstance(w["Valuation"], float)
        assert w["Valuation"] == pytest.approx(65.0)

    def test_pro_score_with_decimal_preserved(self, tmp_path):
        # Scores like "93.5" should survive float coercion accurately
        tsv = self._make_tsv(tmp_path, {"WA": "93.5"})
        w = parse_inventory(tsv)[0]
        assert w["WA"] == pytest.approx(93.5)

    def test_invalid_pro_score_string_is_none(self, tmp_path):
        # A non-numeric string in a pro score field should yield None
        tsv = self._make_tsv(tmp_path, {"WA": "N/A"})
        w = parse_inventory(tsv)[0]
        assert w["WA"] is None

    def test_invalid_cnotes_string_is_none(self, tmp_path):
        tsv = self._make_tsv(tmp_path, {"cNotes": "many"})
        w = parse_inventory(tsv)[0]
        assert w["cNotes"] is None

    def test_invalid_price_string_is_none(self, tmp_path):
        tsv = self._make_tsv(tmp_path, {"Price": "ask"})
        w = parse_inventory(tsv)[0]
        assert w["Price"] is None


# ---------------------------------------------------------------------------
# TestTotalValuation
# ---------------------------------------------------------------------------


class TestTotalValuation:
    """TotalValuation is the sum of Valuation across all bottles in a group."""

    def test_two_bottles_same_wine_summed(self, tmp_path):
        # Two bottles of the same (iWine, Vintage) with known Valuations
        row1 = _minimal_row({"Valuation": "60.00"})
        row2 = _minimal_row({"Valuation": "65.00"})
        tsv = _write_tsv(tmp_path, ALL_FIELDS, [row1, row2])
        w = parse_inventory(tsv)[0]
        assert w["Quantity"] == 2
        assert w["TotalValuation"] == pytest.approx(125.0)

    def test_three_bottles_summed(self, tmp_path):
        rows = [_minimal_row({"Valuation": str(v)}) for v in [50.0, 55.0, 60.0]]
        tsv = _write_tsv(tmp_path, ALL_FIELDS, rows)
        w = parse_inventory(tsv)[0]
        assert w["Quantity"] == 3
        assert w["TotalValuation"] == pytest.approx(165.0)

    def test_total_valuation_rounded_to_two_decimal_places(self, tmp_path):
        rows = [
            _minimal_row({"Valuation": "33.333"}),
            _minimal_row({"Valuation": "33.333"}),
            _minimal_row({"Valuation": "33.334"}),
        ]
        tsv = _write_tsv(tmp_path, ALL_FIELDS, rows)
        w = parse_inventory(tsv)[0]
        assert w["TotalValuation"] == pytest.approx(100.0, abs=0.01)

    def test_mixed_present_and_missing_valuation(self, tmp_path):
        # One bottle has a Valuation, the other does not — only present ones sum
        row1 = _minimal_row({"Valuation": "75.00"})
        row2 = _minimal_row({"Valuation": ""})
        tsv = _write_tsv(tmp_path, ALL_FIELDS, [row1, row2])
        w = parse_inventory(tsv)[0]
        assert w["Quantity"] == 2
        assert w["TotalValuation"] == pytest.approx(75.0)

    def test_different_wines_have_independent_totals(self, tmp_path):
        # Two wines with different iWine IDs must not share their TotalValuation
        row_a = _minimal_row({"iWine": "100001", "Valuation": "50.00"})
        row_b = _minimal_row(
            {
                "iWine": "100002",
                "Wine": "Other Wine",
                "Valuation": "80.00",
            }
        )
        tsv = _write_tsv(tmp_path, ALL_FIELDS, [row_a, row_b])
        wines = parse_inventory(tsv)
        assert len(wines) == 2
        totals = {w["iWine"]: w["TotalValuation"] for w in wines}
        assert totals["100001"] == pytest.approx(50.0)
        assert totals["100002"] == pytest.approx(80.0)


# ---------------------------------------------------------------------------
# TestTotalValuationAllMissing
# ---------------------------------------------------------------------------


class TestTotalValuationAllMissing:
    """When every bottle in a group has no Valuation, TotalValuation should be None."""

    def test_single_bottle_no_valuation(self, tmp_path):
        row = _minimal_row({"Valuation": ""})
        tsv = _write_tsv(tmp_path, ALL_FIELDS, [row])
        w = parse_inventory(tsv)[0]
        assert w["TotalValuation"] is None

    def test_multiple_bottles_all_missing(self, tmp_path):
        rows = [_minimal_row({"Valuation": ""}) for _ in range(3)]
        tsv = _write_tsv(tmp_path, ALL_FIELDS, rows)
        w = parse_inventory(tsv)[0]
        assert w["TotalValuation"] is None

    def test_total_valuation_none_when_column_absent(self, tmp_path):
        # TSV has no Valuation column at all (original fields only)
        row = _minimal_row()
        tsv = _write_tsv(tmp_path, ORIGINAL_FIELDS, [row])
        w = parse_inventory(tsv)[0]
        assert w["TotalValuation"] is None


# ---------------------------------------------------------------------------
# TestProScoreNone
# ---------------------------------------------------------------------------


class TestProScoreNone:
    """When a wine has no pro score data, all PRO_SCORE_FIELDS must be None."""

    def test_all_pro_scores_none_when_empty(self, tmp_path):
        # TSV has all columns but pro score fields are empty
        row = _minimal_row()  # no WA/WS/etc values
        tsv = _write_tsv(tmp_path, ALL_FIELDS, [row])
        w = parse_inventory(tsv)[0]
        for field in PRO_SCORE_FIELDS:
            assert w[field] is None, (
                f"Expected {field} to be None for wine with no scores"
            )

    def test_pro_scores_none_independent_of_ct(self, tmp_path):
        # CT present but all pro scores absent — should not affect each other
        row = _minimal_row({"CT": "92"})
        tsv = _write_tsv(tmp_path, ALL_FIELDS, [row])
        w = parse_inventory(tsv)[0]
        assert w["CT"] == pytest.approx(92.0)
        for field in PRO_SCORE_FIELDS:
            assert w[field] is None


# ---------------------------------------------------------------------------
# TestPurchaseDatePreserved
# ---------------------------------------------------------------------------


class TestPurchaseDatePreserved:
    """PurchaseDate must remain a string — no numeric coercion applied."""

    def test_purchase_date_preserved_as_string(self, tmp_path):
        row = _minimal_row({"PurchaseDate": "2019-06-15"})
        tsv = _write_tsv(tmp_path, ALL_FIELDS, [row])
        w = parse_inventory(tsv)[0]
        assert isinstance(w["PurchaseDate"], str)
        assert w["PurchaseDate"] == "2019-06-15"

    def test_purchase_date_empty_stays_string(self, tmp_path):
        row = _minimal_row({"PurchaseDate": ""})
        tsv = _write_tsv(tmp_path, ALL_FIELDS, [row])
        w = parse_inventory(tsv)[0]
        # Empty string is stripped to "" — still a string, not None
        assert isinstance(w["PurchaseDate"], str)

    def test_store_name_preserved_as_string(self, tmp_path):
        row = _minimal_row({"StoreName": "K&L Wine Merchants"})
        tsv = _write_tsv(tmp_path, ALL_FIELDS, [row])
        w = parse_inventory(tsv)[0]
        assert isinstance(w["StoreName"], str)
        assert w["StoreName"] == "K&L Wine Merchants"

    def test_purchase_date_not_coerced_to_int(self, tmp_path):
        # A year-only date like "2019" must not become the integer 2019
        row = _minimal_row({"PurchaseDate": "2019"})
        tsv = _write_tsv(tmp_path, ALL_FIELDS, [row])
        w = parse_inventory(tsv)[0]
        assert w["PurchaseDate"] == "2019"
        assert not isinstance(w["PurchaseDate"], int)


# ---------------------------------------------------------------------------
# TestDateRelativeFixture
# ---------------------------------------------------------------------------


class TestDateRelativeFixture:
    """The shared inventory fixture parses to a known set of wines."""

    def test_fixture_groups_six_bottles_into_five_wines(self, tmp_path):
        wines = parse_inventory(build_inventory_tsv(tmp_path / "inv.tsv"))
        by_id = {w["iWine"]: w["Quantity"] for w in wines}
        assert by_id == {
            "200001": 2,
            "200002": 1,
            "200003": 1,
            "200004": 1,
            "200005": 1,
        }

    def test_fixture_windows_follow_the_given_year(self, tmp_path):
        from datetime import date

        wines = parse_inventory(
            build_inventory_tsv(tmp_path / "inv.tsv", date(2040, 3, 1))
        )
        barolo = next(w for w in wines if w["iWine"] == "200005")
        assert (barolo["BeginConsume"], barolo["EndConsume"]) == (2048, 2060)

    def test_fixture_has_mixed_types(self, tmp_path):
        wines = parse_inventory(build_inventory_tsv(tmp_path / "inv.tsv"))
        assert {w["Type"] for w in wines} == {"Red", "White", "Sparkling", "Rosé"}
