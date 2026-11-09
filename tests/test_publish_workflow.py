"""Static checks of the workflow that publishes the image to GHCR."""

import re
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parent.parent
WORKFLOW = REPO / ".github/workflows/publish-image.yml"
IMAGE = "ghcr.io/unibrain1/thesommelier"


@pytest.fixture(scope="module")
def workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def steps(workflow) -> list:
    return workflow["jobs"]["image"]["steps"]


def _triggers(workflow: dict) -> dict:
    # PyYAML reads the bare key `on` as the boolean True.
    return workflow.get("on", workflow.get(True))


def _step(steps: list, action: str) -> dict:
    return next(s for s in steps if s.get("uses", "").startswith(action + "@"))


def _dockerignore_includes() -> list:
    lines = (REPO / ".dockerignore").read_text(encoding="utf-8").splitlines()
    return [line[1:] for line in lines if line.startswith("!")]


def test_the_workflow_has_no_one_time_note():
    # The file is on main after the v4.0.0 release. A note about the time before
    # is stale.
    assert "reaches main" not in WORKFLOW.read_text(encoding="utf-8")


def test_pull_request_build_starts_on_the_build_inputs(workflow):
    pull_request = _triggers(workflow)["pull_request"]

    assert "paths-ignore" not in pull_request
    assert {
        "Dockerfile",
        ".dockerignore",
        ".github/workflows/publish-image.yml",
    } <= set(pull_request["paths"])


def test_the_dockerignore_allow_list_is_read():
    # An empty list would skip the parametrized test below instead of failing it.
    assert {"requirements.txt", "nginx.conf"} <= set(_dockerignore_includes())


@pytest.mark.parametrize("pattern", _dockerignore_includes())
def test_every_image_file_pattern_starts_the_pull_request_build(workflow, pattern):
    paths = _triggers(workflow)["pull_request"]["paths"]

    # Exact match: Python's fnmatch lets `*` cross `/`, GitHub's path filter does not.
    assert pattern in paths


def test_the_image_is_built_for_both_architectures(steps):
    build = _step(steps, "docker/build-push-action")

    assert build["with"]["platforms"] == "linux/amd64,linux/arm64"


def test_a_pull_request_pushes_nothing_and_does_not_log_in(steps):
    build = _step(steps, "docker/build-push-action")
    login = _step(steps, "docker/login-action")

    assert build["with"]["push"] == "${{ github.event_name != 'pull_request' }}"
    assert login["if"] == "github.event_name != 'pull_request'"


def test_a_version_tag_main_and_a_manual_run_start_the_workflow(workflow):
    triggers = _triggers(workflow)

    assert triggers["push"]["tags"] == ["v[0-9]+.[0-9]+.[0-9]+*"]
    # The milestone branch pushes edge because a manual run needs the file on main.
    assert triggers["push"]["branches"] == ["main", "milestone/**"]
    assert "workflow_dispatch" in triggers


def test_the_tags_follow_the_release_rules(steps):
    meta = _step(steps, "docker/metadata-action")["with"]
    tags = meta["tags"]

    assert "latest=false" in meta["flavor"]
    # A manual run on an old tag must not rebuild 4.0.0 or move latest back.
    push_only = "enable=${{ github.event_name == 'push' }}"
    assert f"type=semver,pattern={{{{version}}}},{push_only}" in tags
    assert f"type=semver,pattern={{{{major}}}}.{{{{minor}}}},{push_only}" in tags
    # A prerelease tag (v4.1.0-rc1) must not move latest.
    assert (
        "type=raw,value=latest,enable=${{ github.event_name == 'push' "
        "&& startsWith(github.ref, 'refs/tags/v') && !contains(github.ref, '-') }}"
    ) in tags
    # A branch pushes edge. A manual run on a tag must not overwrite it.
    assert (
        "type=raw,value=edge,enable=${{ startsWith(github.ref, 'refs/heads/') }}"
        in tags
    )


def test_the_checkout_does_not_keep_the_token(steps):
    checkout = _step(steps, "actions/checkout")

    assert checkout["with"]["persist-credentials"] is False


def test_the_image_name_matches_the_sample(steps):
    meta = _step(steps, "docker/metadata-action")["with"]
    sample = yaml.safe_load(
        (REPO / "examples/compose.yaml").read_text(encoding="utf-8")
    )

    assert meta["images"] == IMAGE
    assert sample["services"]["the-sommelier"]["image"].startswith(IMAGE + ":")


def test_the_build_takes_its_labels_from_the_metadata_step(steps):
    meta = _step(steps, "docker/metadata-action")
    build = _step(steps, "docker/build-push-action")

    assert build["with"]["labels"] == f"${{{{ steps.{meta['id']}.outputs.labels }}}}"
    assert build["with"]["tags"] == f"${{{{ steps.{meta['id']}.outputs.tags }}}}"


def test_each_action_is_pinned_by_commit_sha_with_a_version_comment():
    pinned = re.compile(r"^\s*-?\s*uses:\s+[\w./-]+@[0-9a-f]{40}\s+# v\d+\S*$")
    uses = [
        line
        for line in WORKFLOW.read_text(encoding="utf-8").splitlines()
        if re.match(r"^\s*-?\s*uses:", line)
    ]

    assert uses
    assert all(pinned.match(line) for line in uses)


def test_only_the_job_can_write_packages(workflow):
    assert workflow["permissions"] == {"contents": "read"}
    assert workflow["jobs"]["image"]["permissions"] == {
        "contents": "read",
        "packages": "write",
    }


def test_only_the_build_step_pushes(steps):
    # A `run: docker push` step would skip the pull request gate on the build step.
    for step in steps:
        if step.get("uses", "").startswith("docker/build-push-action@"):
            continue
        assert "push" not in step.get("run", "")


def test_a_manual_run_on_a_tag_fails_before_the_build(steps):
    guard = steps[0]

    assert guard["if"] == (
        "github.event_name == 'workflow_dispatch' && startsWith(github.ref, 'refs/tags/')"
    )
    assert "exit 1" in guard["run"]
