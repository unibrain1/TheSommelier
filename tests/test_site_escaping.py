"""Static checks that site/index.html escapes data before an HTML write.

The site has no JS test runner. These checks read the page source. They find
a field of a data variable that joins an HTML string without escapeHtml().
The scan knows only the variable names in DATA_VARS. A field read through
another name, or copied to a local variable first, is not found.
site/picklist.html gets a smaller check: it must have no HTML write at all.
"""

import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
INDEX = REPO / "site" / "index.html"
PICKLIST = REPO / "site" / "picklist.html"

# The names that index.html uses for an object from plan.json or
# pairing_suggestions.json (a week, a quarter, a suggestion, a changelog
# entry), and for a caught error.
DATA_VARS = ("w", "info", "s", "sb", "p", "cp", "c", "err")

# `+` or `+=`, then an optional `(`, then a data variable with `.` or `[`.
# \s also matches a line break, so a join split over two lines is found.
RAW_FIELD = re.compile(
    r"\+=?\s*\(?\s*\b(?:" + "|".join(DATA_VARS) + r")\s*[.\[]",
)

# Lines that may join data raw. A line is allowed only when it starts with
# the key, after its indent.
ALLOWED = {
    "var mealText = ": "renderCalendar() writes mealText through textContent",
    "mealText += ": "renderCalendar() writes mealText through textContent",
}


def _script(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    blocks = re.findall(r"<script[^>]*>(.*?)</script>", text, re.S)
    assert blocks, f"no <script> in {path}"
    return "\n".join(blocks)


def _raw_fields(script: str) -> list[str]:
    lines = script.splitlines()
    raw = []
    for match in RAW_FIELD.finditer(script):
        line = lines[script.count("\n", 0, match.end())].strip()
        if any(line.startswith(prefix) for prefix in ALLOWED):
            continue
        raw.append(line)
    return raw


def test_no_data_field_is_concatenated_raw():
    raw = _raw_fields(_script(INDEX))

    assert not raw, "data fields joined to HTML without escapeHtml():\n" + "\n".join(
        raw
    )


def test_the_scan_finds_a_raw_field():
    assert _raw_fields("html += '<b>' + w.name + '</b>';")
    assert _raw_fields("html += sb.wine;")
    assert _raw_fields("html += '<b>' +\n      w.name + '</b>';")
    assert _raw_fields("html += '<b>' + (w.name || '') + '</b>';")
    assert _raw_fields("html += '<b>' + w['name'] + '</b>';")
    assert _raw_fields("    var mealText = 'x';\nhtml += '<b>' + w.name;")
    assert not _raw_fields("html += '<b>' + escapeHtml(w.name) + '</b>';")
    assert not _raw_fields("x = '#grid-' + yr + '-' + season;")
    assert not _raw_fields("x = a + sw.length;")
    assert not _raw_fields("    mealText += ' · ' + cp.suggestion.meal;")


def test_index_has_no_template_literal():
    # The scan does not read ${...}, so a template literal could hide a raw field.
    assert "`" not in _script(INDEX)


def test_named_fields_are_escaped():
    script = _script(INDEX)
    for field in (
        "info.title",
        "info.sub",
        "info.note",
        "w.vintage",
        "w.name",
        "w.window",
        "w.score",
    ):
        assert f"escapeHtml({field})" in script, field


def test_badge_class_uses_a_fixed_set():
    script = _script(INDEX)

    assert "'badge badge-' + w.badge" not in script
    assert "function safeBadge(" in script
    assert "safeBadge(w.badge)" in script


def test_picklist_has_no_html_write():
    script = _script(PICKLIST)
    for sink in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write"):
        assert sink not in script, sink
