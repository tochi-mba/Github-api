"""Raw GitHub payloads become fixed public models, and nothing else survives."""

from __future__ import annotations

from typing import Any

import pytest

from github_api.github import mappers
from github_api.github.models import GitHubCredential, Repo

EXTRA = {"node_id": "MDQ6", "avatar_url": "https://avatars.test/a.png", "site_admin": False}


def test_readers_forgive_missing_and_malformed_fields() -> None:
    assert mappers.text(None, "a") == ""
    assert mappers.number({"n": True}, "n", 7) == 7
    assert mappers.number({"n": "x"}, "n") == 0
    assert mappers.number({"n": "12"}, "n") == 12
    assert mappers.flag({"f": "yes"}, "f") is False
    assert mappers.nested({"a": []}, "a", "b") == {}
    assert mappers.rows({"r": [1, {"a": 1}]}, "r") == [{"a": 1}]
    assert mappers.rows({"r": "x"}, "r") == []
    assert mappers.login({"author": None}) == "ghost"


@pytest.mark.parametrize(
    ("state", "word"),
    [("SUCCESS", "success"), ("ERROR", "failure"), ("EXPECTED", "pending"), (None, "none")],
)
def test_rollup_states_become_four_words(state: Any, word: str) -> None:
    assert mappers.rollup(state) == word


def test_a_repository_node_projects_and_drops_unknown_fields() -> None:
    node = {
        **EXTRA,
        "nameWithOwner": "octo/hello",
        "isPrivate": True,
        "description": None,
        "url": "https://github.test/octo/hello",
        "defaultBranchRef": {
            "name": "main",
            "target": {"statusCheckRollup": {"state": "FAILURE", "contexts": {}}},
        },
        "pullRequests": {"totalCount": 2},
        "issues": {"totalCount": 3},
        "owner": {"login": "octo"},
    }
    assert mappers.repo_node(node).model_dump() == {
        "full_name": "octo/hello",
        "private": True,
        "default_branch": "main",
        "description": "",
        "open_pulls": 2,
        "open_issues": 3,
        "ci": "failure",
        "url": "https://github.test/octo/hello",
    }
    empty = mappers.repo_node({"nameWithOwner": "octo/empty", "defaultBranchRef": None})
    assert (empty.default_branch, empty.ci) == ("", "none")


def test_a_new_repository_from_rest() -> None:
    repo = mappers.repo_rest(
        {
            **EXTRA,
            "full_name": "octo/new",
            "private": False,
            "default_branch": "main",
            "description": "d",
            "open_issues_count": 0,
            "html_url": "u",
        }
    )
    assert repo == Repo(full_name="octo/new", default_branch="main", description="d", url="u")


def test_a_pull_request_node() -> None:
    node = {
        **EXTRA,
        "number": 42,
        "title": "Add",
        "state": "OPEN",
        "isDraft": False,
        "url": "u",
        "headRefName": "feature",
        "baseRefName": "main",
        "mergeStateStatus": "HAS_HOOKS",
        "author": {"login": "octo", "avatarUrl": "x"},
        "repository": {"nameWithOwner": "octo/hello"},
        "commits": {"nodes": [{"commit": {"statusCheckRollup": {"state": "PENDING"}}}]},
    }
    pull = mappers.pull_node(node)
    assert set(pull.model_dump()) == {
        "repo",
        "number",
        "title",
        "state",
        "author",
        "draft",
        "head",
        "base",
        "mergeable",
        "checks",
        "url",
    }
    assert (pull.state, pull.mergeable, pull.checks) == ("open", "clean", "pending")
    bare = mappers.pull_node({"number": 1, "mergeStateStatus": "WHATEVER"})
    assert (bare.mergeable, bare.checks, bare.author) == ("unknown", "none", "ghost")


def test_reviews_threads_and_the_latest_review() -> None:
    assert mappers.review_node({"author": {"login": "r"}, "state": "CHANGES_REQUESTED"}).state == (
        "changes_requested"
    )
    thread = mappers.thread_node(
        {
            "isResolved": True,
            "path": "a.py",
            "line": None,
            "originalLine": 9,
            "comments": {"nodes": [{"author": {"login": "r"}, "body": "nit", "id": "x"}]},
        }
    )
    assert thread.model_dump() == {
        "path": "a.py",
        "line": 9,
        "author": "r",
        "body": "nit",
        "resolved": True,
    }
    assert mappers.thread_node({"path": "b.py"}).author == ""

    latest = mappers.latest_review(
        {"reviews": {"totalCount": 4, "nodes": [{"author": None, "state": "APPROVED"}]}}
    )
    assert (latest.total, latest.latest and latest.latest.author) == (4, "ghost")
    assert mappers.latest_review({"reviews": {"totalCount": 0, "nodes": []}}).latest is None


def test_check_runs_status_contexts_and_unknown_kinds() -> None:
    run = mappers.check_context(
        {
            **EXTRA,
            "__typename": "CheckRun",
            "databaseId": 991,
            "name": "tests",
            "status": "COMPLETED",
            "conclusion": "FAILURE",
            "detailsUrl": "u",
            "checkSuite": {"workflowRun": {"workflow": {"name": "CI"}}},
            "steps": {
                "nodes": [
                    {"name": "setup", "conclusion": "SUCCESS"},
                    {"name": "pytest", "conclusion": "FAILURE"},
                    {"name": "slow", "conclusion": "TIMED_OUT"},
                ]
            },
        }
    )
    assert run is not None
    assert run.model_dump() == {
        "id": "991",
        "name": "tests",
        "status": "completed",
        "conclusion": "failure",
        "workflow": "CI",
        "failing_steps": ["pytest", "slow"],
        "url": "u",
    }
    status = mappers.check_context(
        {"__typename": "StatusContext", "context": "ci/legacy", "state": "PENDING"}
    )
    assert status is not None
    assert (status.id, status.status, status.conclusion) == ("", "in_progress", "")
    odd = mappers.check_context({"__typename": "StatusContext", "state": "WEIRD"})
    assert odd is not None
    assert odd.status == "queued"
    assert mappers.check_context({"__typename": "Other"}) is None


def test_a_rollup_or_its_absence() -> None:
    found = mappers.checks({"state": "SUCCESS", "contexts": {"nodes": [{"__typename": "Other"}]}})
    assert (found.summary, found.checks) == ("success", [])
    assert mappers.checks(None).summary == "none"
    assert mappers.last_commit_rollup({}) == {}


@pytest.mark.parametrize(
    ("payload", "patch", "truncated"),
    [
        ({"patch": "@@ x", "additions": 1}, "@@ x", False),
        ({"patch": "y" * 10, "additions": 1}, "y" * 5, True),
        ({"additions": 0, "deletions": 0, "status": "added"}, "", True),
        ({"additions": 500, "status": "modified"}, "", True),
        ({"status": "renamed"}, "", False),
    ],
)
def test_a_changed_file_says_when_its_patch_is_cut(
    payload: dict[str, Any], patch: str, truncated: bool
) -> None:
    changed = mappers.changed_file(
        {**EXTRA, "filename": "b.py", "previous_filename": "a.py", "sha": "s", **payload}, 5
    )
    assert (changed.path, changed.previous_path) == ("b.py", "a.py")
    assert (changed.patch, changed.patch_truncated) == (patch, truncated)
    assert "sha" not in changed.model_dump()


def test_issues_from_graphql_and_rest() -> None:
    node = mappers.issue_node(
        {
            **EXTRA,
            "number": 7,
            "title": "Bug",
            "state": "CLOSED",
            "url": "u",
            "author": {"login": "octo"},
            "repository": {"nameWithOwner": "octo/hello"},
            "labels": {"nodes": [{"name": "bug", "color": "f00"}]},
        }
    )
    assert (node.state, node.labels) == ("closed", ["bug"])
    rest = mappers.issue_rest(
        {
            **EXTRA,
            "number": 7,
            "title": "Bug",
            "state": "open",
            "user": {"login": "octo"},
            "labels": [{"name": "bug", "id": 1}],
            "html_url": "u",
            "reactions": {},
        },
        "octo/hello",
    )
    assert rest.model_dump() == {
        "repo": "octo/hello",
        "number": 7,
        "title": "Bug",
        "state": "open",
        "author": "octo",
        "labels": ["bug"],
        "url": "u",
    }


def test_small_projections() -> None:
    assert mappers.tree_entry({"path": "src", "type": "tree", "sha": "s"}).model_dump() == {
        "path": "src",
        "type": "dir",
        "size": 0,
    }
    assert mappers.tree_entry({"path": "x", "type": "odd"}).type == "odd"
    assert mappers.reviewed({"state": "APPROVED", "html_url": "u", "id": 1}).model_dump() == {
        "state": "approved",
        "url": "u",
    }
    assert mappers.merged({"merged": True, "sha": "s", "message": "m", "x": 1}).merged is True
    job = mappers.job_status(
        {"id": 5, "name": "tests", "status": "completed", "conclusion": None, "html_url": "u"}
    )
    assert (job.id, job.conclusion) == ("5", "")


def test_a_credential_never_shows_its_headers() -> None:
    cred = GitHubCredential(headers={"Authorization": "Bearer ghu_secret"}, kind="app")
    assert "ghu_secret" not in repr(cred)
    assert "ghu_secret" not in str(cred)
    assert "1 redacted" in repr(cred)
