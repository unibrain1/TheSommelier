"""Static checks of the version footer of the site pages."""

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
PAGES = ("site/index.html", "site/picklist.html")


def _page(page: str) -> str:
    return (REPO / page).read_text(encoding="utf-8")


def _version_fetch(text: str) -> str:
    """Return the statement that starts with fetch('version.json')."""
    start = text.index("fetch('version.json')")
    end = text.index(".catch(function() {});", start)
    return text[start : end + len(".catch(function() {});")]


@pytest.mark.parametrize("page", PAGES)
def test_the_footer_shows_unknown_until_the_version_loads(page):
    footer = re.search(r"<footer>(.*?)</footer>", _page(page), re.S)

    assert footer, page
    assert '<span id="app-version">unknown</span>' in footer.group(1)


@pytest.mark.parametrize("page", PAGES)
def test_the_footer_comes_before_the_script_that_fills_it(page):
    text = _page(page)

    # A footer after the script can be missing when the fetch resolves. The
    # empty catch then hides the error, and the footer keeps unknown.
    assert text.index('id="app-version"') < text.index("<script>")


@pytest.mark.parametrize("page", PAGES)
def test_the_page_sets_the_version_with_text_content_only(page):
    text = _page(page)
    fetch = _version_fetch(text)

    assert "getElementById('app-version').textContent = v.version" in fetch
    assert "innerHTML" not in fetch
    assert not re.search(r"app-version'\)\.innerHTML", text)


@pytest.mark.parametrize("page", PAGES)
def test_a_missing_version_keeps_unknown_and_does_not_stop_the_page(page):
    text = _page(page)
    fetch = _version_fetch(text)
    plan_load = text[text.index("Promise.all([") : text.index("]).then(")]

    assert "if (!r.ok) return;" in fetch
    assert "typeof v.version === 'string'" in fetch
    assert fetch.endswith(".catch(function() {});")
    assert "version.json" not in plan_load
