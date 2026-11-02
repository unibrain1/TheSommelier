"""Path behavior: scripts read and write in DATA_DIR and SITE_DIR, not in fixed folders."""

import base64
import importlib.util
import json
import os
import subprocess
import sys
import types
from pathlib import Path

import pytest

import settings

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "scripts"

SETTING_VARS = settings.ALL_VARS


@pytest.fixture
def clean_env(monkeypatch):
    for name in SETTING_VARS:
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


@pytest.fixture
def dirs(clean_env, tmp_path, monkeypatch):
    """DATA_DIR and SITE_DIR in tmp_path, and a different cwd that stays empty."""
    data = tmp_path / "datadir"
    site = tmp_path / "somewhere" / "sitedir"
    cwd = tmp_path / "cwd"
    for d in (data, site, cwd):
        d.mkdir(parents=True)
    clean_env.setenv("DATA_DIR", str(data))
    clean_env.setenv("SITE_DIR", str(site))
    monkeypatch.chdir(cwd)
    return types.SimpleNamespace(data=data, site=site, cwd=cwd, root=tmp_path)


@pytest.fixture
def generate_plan(clean_env):
    """Load generate_plan with the sample plan.yaml.

    The real config/plan.yaml is git-ignored and not in CI.
    """
    clean_env.setenv("PLAN_CONFIG", str(REPO / "config" / "plan.yaml.sample"))
    spec = importlib.util.spec_from_file_location(
        "generate_plan_paths_test", SCRIPTS / "generate_plan.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_script(args, values, cwd):
    env = {"PATH": os.environ["PATH"], **values}
    return subprocess.run(
        [sys.executable, *args], env=env, cwd=cwd, capture_output=True, text=True
    )


def inventory():
    wine = {
        "iWine": "1", "Wine": "Test Pinot", "Vintage": 2018, "Quantity": 60,
        "BeginConsume": 2020, "EndConsume": 2040, "Type": "Red",
        "Varietal": "Pinot Noir", "Producer": "P", "Location": "Cellar",
        "Bin": "A1", "CT": 90.0,
    }  # fmt: skip
    return [wine]


# --- generate_plan --------------------------------------------------------


class TestGeneratePlanPaths:
    def test_main_reads_live_plan_from_site_dir_and_writes_backup_to_data_dir(
        self, dirs, generate_plan, monkeypatch
    ):
        live = {
            "allWeeks": [],
            "changelogHistory": [
                {"date": "2999-01-01", "changes": [], "marker": "LIVE-MARK"}
            ],
        }
        (dirs.site / "plan.json").write_text(json.dumps(live), encoding="utf-8")
        inv = dirs.root / "inventory.json"
        inv.write_text(json.dumps(inventory()), encoding="utf-8")
        out = dirs.root / "out" / "plan.json"
        monkeypatch.setattr(sys, "argv", ["generate_plan.py", str(inv), str(out)])

        generate_plan.main()

        backup = dirs.data / "plan_previous.json"
        assert backup.exists()
        assert json.loads(backup.read_text(encoding="utf-8")) == live
        new_plan = json.loads(out.read_text(encoding="utf-8"))
        assert len(new_plan["allWeeks"]) == 52
        assert any(e.get("marker") == "LIVE-MARK" for e in new_plan["changelogHistory"])
        assert list(dirs.cwd.iterdir()) == []
        assert not (dirs.site / "plan_previous.json").exists()

    def test_main_without_live_plan_writes_no_backup(
        self, dirs, generate_plan, monkeypatch
    ):
        inv = dirs.root / "inventory.json"
        inv.write_text(json.dumps(inventory()), encoding="utf-8")
        out = dirs.root / "out.json"
        monkeypatch.setattr(sys, "argv", ["generate_plan.py", str(inv), str(out)])
        generate_plan.main()
        assert out.exists()
        assert not (dirs.data / "plan_previous.json").exists()
        assert list(dirs.cwd.iterdir()) == []

    def test_main_without_live_plan_logs_empty_history(
        self, dirs, generate_plan, monkeypatch, capsys
    ):
        inv = dirs.root / "inventory.json"
        inv.write_text(json.dumps(inventory()), encoding="utf-8")
        out = dirs.root / "out.json"
        monkeypatch.setattr(sys, "argv", ["generate_plan.py", str(inv), str(out)])
        generate_plan.main()
        assert "changelog history starts empty" in capsys.readouterr().out

    def test_main_backup_failure_keeps_live_history(
        self, dirs, generate_plan, monkeypatch, capsys
    ):
        live = {
            "allWeeks": [],
            "changelogHistory": [
                {"date": "2999-01-01", "changes": [], "marker": "LIVE-MARK"}
            ],
        }
        (dirs.site / "plan.json").write_text(json.dumps(live), encoding="utf-8")
        # A folder in place of the backup file makes the write fail.
        (dirs.data / "plan_previous.json").mkdir()
        inv = dirs.root / "inventory.json"
        inv.write_text(json.dumps(inventory()), encoding="utf-8")
        out = dirs.root / "out.json"
        monkeypatch.setattr(sys, "argv", ["generate_plan.py", str(inv), str(out)])

        generate_plan.main()

        err = capsys.readouterr().err
        assert "could not back up plan" in err
        assert "could not read existing plan" not in err
        new_plan = json.loads(out.read_text(encoding="utf-8"))
        assert any(e.get("marker") == "LIVE-MARK" for e in new_plan["changelogHistory"])

    def test_load_community_notes_reads_data_dir(self, dirs, generate_plan):
        assert generate_plan._load_community_notes() is None
        notes = {"123": [{"iNote": "9", "body": "from-data-dir"}]}
        (dirs.data / "community_notes.json").write_text(
            json.dumps(notes), encoding="utf-8"
        )
        assert generate_plan._load_community_notes() == notes

    def test_load_community_notes_ignores_cwd_data_folder(self, dirs, generate_plan):
        (dirs.cwd / "data").mkdir()
        (dirs.cwd / "data" / "community_notes.json").write_text(
            '{"1": []}', encoding="utf-8"
        )
        assert generate_plan._load_community_notes() is None


# --- generate_digest ------------------------------------------------------


def _digest_inputs(site: Path):
    (site / "plan.json").write_text(json.dumps({"allWeeks": []}), encoding="utf-8")
    (site / "pairing_suggestions.json").write_text(
        json.dumps({"suggestions": []}), encoding="utf-8"
    )


class TestGenerateDigestPaths:
    def _run_main(self, monkeypatch, force=True):
        import generate_digest

        monkeypatch.setattr(
            sys, "argv", ["generate_digest.py"] + (["--force"] if force else [])
        )
        generate_digest.main()

    def test_main_writes_digest_files_to_site_dir(self, dirs, monkeypatch):
        _digest_inputs(dirs.site)
        self._run_main(monkeypatch)
        assert json.loads((dirs.site / "digest.json").read_text(encoding="utf-8"))[
            "date"
        ]
        assert (
            "<html" in (dirs.site / "digest.html").read_text(encoding="utf-8").lower()
        )
        assert list(dirs.cwd.iterdir()) == []
        assert not (dirs.data / "digest.json").exists()

    def test_main_without_menu_still_writes_files_and_exits_2(self, dirs, monkeypatch):
        _digest_inputs(dirs.site)
        with pytest.raises(SystemExit) as exc:
            self._run_main(monkeypatch, force=False)
        assert exc.value.code == 2
        assert (dirs.site / "digest.json").exists()
        assert (dirs.site / "digest.html").exists()

    def test_main_missing_plan_names_the_real_path(self, dirs, monkeypatch, capsys):
        with pytest.raises(SystemExit) as exc:
            self._run_main(monkeypatch)
        assert exc.value.code == 1
        assert str(dirs.site / "plan.json") in capsys.readouterr().err

    def test_logo_comes_from_code_folder_when_site_dir_has_none(
        self, dirs, monkeypatch
    ):
        logo = REPO / "site" / "logo.png"
        assert logo.exists()
        assert not (dirs.site / "logo.png").exists()
        _digest_inputs(dirs.site)
        self._run_main(monkeypatch)
        html_text = (dirs.site / "digest.html").read_text(encoding="utf-8")
        encoded = base64.b64encode(logo.read_bytes()).decode("ascii")
        assert f"data:image/png;base64,{encoded}" in html_text


# --- fetch_community_notes and enrich_menu (default paths) ------------------

RSS = """<rss version="2.0"><channel><title>t</title>
<item>
  <title>2018 Test Wine</title>
  <link>https://www.cellartracker.com/notes.asp?iWine=111#iNote222</link>
  <guid>https://www.cellartracker.com/notes.asp?iWine=111#iNote222</guid>
  <pubDate>Thu, 16 Apr 2026 00:00:00 PST</pubDate>
  <description>Tasted by someone. (92 pts.) Tasted 4/11/2026
Nice wine.</description>
</item></channel></rss>"""


class TestDefaultPathsFollowDataDir:
    def test_fetch_community_notes_default_cache_is_in_data_dir(self, dirs):
        feed = dirs.root / "feed.xml"
        feed.write_text(RSS, encoding="utf-8")
        result = run_script(
            [str(SCRIPTS / "fetch_community_notes.py")],
            {"DATA_DIR": str(dirs.data), "CT_COMMUNITY_NOTES_RSS": feed.as_uri()},
            cwd=dirs.cwd,
        )
        assert result.returncode == 0, result.stderr
        cache = json.loads(
            (dirs.data / "community_notes.json").read_text(encoding="utf-8")
        )
        assert list(cache) == ["111"]
        assert cache["111"][0]["iNote"] == "222"
        assert list(dirs.cwd.iterdir()) == []

    def test_fetch_community_notes_without_url_exits_1(self, dirs):
        result = run_script(
            [str(SCRIPTS / "fetch_community_notes.py")],
            {"DATA_DIR": str(dirs.data)},
            cwd=dirs.cwd,
        )
        assert result.returncode == 1
        assert not (dirs.data / "community_notes.json").exists()

    def test_enrich_menu_default_outputs_are_in_data_dir(self, dirs):
        menu = dirs.root / "menu.json"
        menu.write_text("[]", encoding="utf-8")
        result = run_script(
            [str(SCRIPTS / "enrich_menu.py"), str(menu)],
            {"DATA_DIR": str(dirs.data)},
            cwd=dirs.cwd,
        )
        assert result.returncode == 0, result.stderr
        assert (
            json.loads((dirs.data / "menu_enriched.json").read_text(encoding="utf-8"))
            == {}
        )
        assert (
            json.loads(
                (dirs.data / "menu_enriched_full.json").read_text(encoding="utf-8")
            )
            == []
        )
        assert list(dirs.cwd.iterdir()) == []


# --- CLIs that need path arguments ------------------------------------------


@pytest.mark.parametrize(
    ("script", "args"),
    [
        ("parse_inventory.py", []),
        ("parse_consumed.py", []),
        ("compare.py", []),
        ("compare.py", ["only-one.json"]),
    ],
)
def test_path_cli_without_arguments_exits_2_with_usage(tmp_path, script, args):
    result = run_script([str(SCRIPTS / script), *args], {}, cwd=tmp_path)
    assert result.returncode == 2
    assert "Usage" in result.stderr
    assert "Traceback" not in result.stderr
    assert list(tmp_path.iterdir()) == []
