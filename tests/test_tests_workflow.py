"""Static checks of the workflow that runs the tests in CI."""

import re
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parent.parent
WORKFLOW = REPO / ".github/workflows/tests.yml"


@pytest.fixture(scope="module")
def workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _triggers(workflow: dict) -> dict:
    # PyYAML reads the bare key `on` as the boolean True.
    return workflow.get("on", workflow.get(True))


def _runs(workflow: dict, job: str) -> list[str]:
    return [s["run"] for s in workflow["jobs"][job]["steps"] if "run" in s]


def test_each_pull_request_and_a_push_to_main_or_a_milestone_start_the_tests(
    workflow,
):
    triggers = _triggers(workflow)

    # A path filter would let a pull request merge with no test run.
    assert triggers["pull_request"] is None
    assert triggers["push"]["branches"] == ["main", "milestone/**"]


def test_a_new_push_cancels_the_older_run_on_the_same_ref(workflow):
    assert workflow["concurrency"] == {
        "group": "${{ github.workflow }}-${{ github.ref }}",
        "cancel-in-progress": True,
    }


def test_the_token_can_only_read(workflow):
    assert workflow["permissions"] == {"contents": "read"}
    for job in workflow["jobs"].values():
        assert "permissions" not in job


def test_the_python_version_is_the_one_of_the_image(workflow):
    dockerfile = (REPO / "Dockerfile").read_text(encoding="utf-8")
    image_version = re.search(r"^FROM python:(\d+\.\d+)-", dockerfile, re.M)

    assert image_version
    for job in workflow["jobs"].values():
        setup = next(
            s
            for s in job["steps"]
            if s.get("uses", "").startswith("actions/setup-python@")
        )
        assert setup["with"]["python-version"] == image_version.group(1)


def test_the_pytest_job_runs_the_full_suite(workflow):
    assert "python -m pytest tests/ -q --tb=short" in _runs(workflow, "pytest")


def test_the_docker_job_turns_on_the_docker_tests_without_a_claude_token(workflow):
    steps = workflow["jobs"]["docker-tests"]["steps"]
    test = next(s for s in steps if "test_docker_image.py" in s.get("run", ""))

    assert test["env"] == {"SOMMELIER_DOCKER_TESTS": "1"}
    assert "SOMMELIER_CLAUDE_TEST_TOKEN" not in yaml.safe_dump(workflow)


def test_the_checkout_does_not_keep_the_token(workflow):
    for job in workflow["jobs"].values():
        checkout = next(
            s for s in job["steps"] if s.get("uses", "").startswith("actions/checkout@")
        )
        assert checkout["with"]["persist-credentials"] is False


def test_each_action_is_pinned_by_commit_sha_with_a_version_comment():
    pinned = re.compile(r"^\s*-?\s*uses:\s+[\w./-]+@[0-9a-f]{40}\s+# v\d+\S*$")
    uses = [
        line
        for line in WORKFLOW.read_text(encoding="utf-8").splitlines()
        if re.match(r"^\s*-?\s*uses:", line)
    ]

    assert uses
    assert all(pinned.match(line) for line in uses)
