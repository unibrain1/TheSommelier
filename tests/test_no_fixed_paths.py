"""Static checks: no script has a fixed data/ or site/ path."""

import ast
import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

SHELL_FILES = [
    "pipeline.sh",
    "send_digest.sh",
    "fetch.sh",
    "fetch_docker.sh",
    "entrypoint.sh",
    "sync.sh",
]
APP_PATH_IN_SHELL = re.compile(r"/app/(data|site)\b")
FIXED_PATH_IN_SHELL = re.compile(r"""(^|[ "'=>(])(data|site)/""")

# (file, string) -> reason. Each entry is one allowed constant.
ALLOWED = {
    ("scripts/generate_digest.py", "site"): (
        "logo.png is a static file of the code (the code folder's site/), "
        "not run output, so it is not in SITE_DIR"
    ),
    ("scripts/settings.py", "site"): (
        "the default SITE_DIR is the 'site' folder inside DATA_DIR"
    ),
}


def _docstring_ids(tree: ast.AST) -> set[int]:
    ids = set()
    for node in ast.walk(tree):
        if isinstance(
            node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        ):
            first = node.body[0] if node.body else None
            if (
                isinstance(first, ast.Expr)
                and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)
            ):
                ids.add(id(first.value))
    return ids


def _is_fixed_path(value: str) -> bool:
    return value.startswith(("data/", "site/")) or value in ("data", "site")


def _scan_source(rel: str, source: str) -> tuple[list[str], set[tuple[str, str]]]:
    hits, used = [], set()
    tree = ast.parse(source)
    skip = _docstring_ids(tree)
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in skip
            and _is_fixed_path(node.value)
        ):
            if (rel, node.value) in ALLOWED:
                used.add((rel, node.value))
            else:
                hits.append(f"{rel}:{node.lineno}: {node.value!r}")
    return hits, used


def _find_fixed_paths() -> tuple[list[str], set[tuple[str, str]]]:
    hits, used = [], set()
    files = sorted((REPO / "scripts").glob("*.py"))
    assert files, "no scripts scanned"
    for path in files:
        rel = path.relative_to(REPO).as_posix()
        h, u = _scan_source(rel, path.read_text(encoding="utf-8"))
        hits += h
        used |= u
    return hits, used


def test_no_fixed_data_or_site_paths_in_python():
    hits, _ = _find_fixed_paths()
    assert hits == []


def test_allow_list_entries_are_used():
    _, used = _find_fixed_paths()
    assert used == set(ALLOWED)


def test_scan_finds_a_fixed_path_when_one_exists():
    """The scan itself can fail: a data/ string in code is found."""
    source = '"""data/in-docstring"""\nx = "data/plan.json"\ny = f"site/{x}"\n'
    hits, _ = _scan_source("scripts/x.py", source)
    assert hits == ["scripts/x.py:2: 'data/plan.json'", "scripts/x.py:3: 'site/'"]


def test_no_fixed_paths_in_shell():
    hits = []
    for name in SHELL_FILES:
        path = REPO / name
        assert path.exists(), name
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if FIXED_PATH_IN_SHELL.search(line):
                hits.append(f"{name}:{number}: {line.strip()}")
    assert hits == []


def test_no_app_data_or_app_site_in_shell():
    """The container paths come from compose (DATA_DIR, SITE_DIR), not from scripts."""
    hits = []
    for name in SHELL_FILES:
        path = REPO / name
        assert path.exists(), name
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if APP_PATH_IN_SHELL.search(line):
                hits.append(f"{name}:{number}: {line.strip()}")
    assert hits == []
