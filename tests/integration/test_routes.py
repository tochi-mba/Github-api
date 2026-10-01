"""Every route the hub calls, with the request it sends and the keys it reads back.

The shapes are the hub's (`lucy_api/clients/repos.py` in LUCY-assistant). Each test sends
what that client sends and asserts the exact key set of what comes back, so a key this
service renames -- or a raw payload that leaks through -- fails here first.
"""

from __future__ import annotations

from github_api.github.errors import GitHubConflictError
from github_api.github.fake import FAKE_SHA
from github_api.github.models import (
    ChangedFile,
    CheckRun,
    Checks,
    Issue,
    Pull,
    PullDetail,
    Review,
    Thread,
)
from tests.conftest import REPO, Harness, headers

BASE = f"/v1/repos/{REPO}"
REPO_KEYS = {
    "full_name",
    "private",
    "default_branch",
    "description",
    "open_pulls",
    "open_issues",
    "ci",
    "url",
}
PULL_KEYS = {
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
CHECK_KEYS = {"id", "name", "status", "conclusion", "workflow", "failing_steps", "url"}
ISSUE_KEYS = {"repo", "number", "title", "state", "author", "labels", "url"}

CHECK = CheckRun(
    id="991",
    name="tests",
    status="completed",
    conclusion="failure",
    workflow="CI",
    failing_steps=["pytest"],
)
PULL = Pull(repo=REPO, number=42, title="Add the thing", state="open", head="feature")


def seed_pull(harness: Harness) -> None:
    harness.gateway.seed_pull(
        PullDetail(
            pull=PULL,
            body="Why",
            reviews=[Review(author="rev", state="approved", body="ok")],
            threads=[Thread(path="a.py", line=3, author="rev", body="nit")],
            checks=[CHECK],
        )
    )


async def test_repositories(harness: Harness) -> None:
    http = harness.http
    found = await http.get("/v1/repos", params={"query": "hel", "limit": 10}, headers=headers())
    assert found.status_code == 200
    assert set(found.json()) == {"repos", "total"}
    assert set(found.json()["repos"][0]) == REPO_KEYS

    one = await http.get(BASE, headers=headers())
    assert one.json()["full_name"] == REPO

    created = await http.post(
        "/v1/repos", json={"name": "new", "visibility": "private"}, headers=headers()
    )
    assert created.status_code == 201
    assert created.json()["full_name"] == "octo/new"
    again = await http.post("/v1/repos", json={"name": "new"}, headers=headers())
    assert again.status_code == 409
    assert again.json()["code"] == "conflict"

    changed = await http.patch(
        "/v1/repos/octo/new", json={"visibility": "public"}, headers=headers()
    )
    assert changed.json()["private"] is False
    nothing = await http.patch("/v1/repos/octo/new", json={}, headers=headers())
    assert nothing.status_code == 422
    assert "say what to change" in nothing.json()["detail"]

    deleted = await http.delete("/v1/repos/octo/new", headers=headers())
    assert deleted.status_code == 204
    gone = await http.get("/v1/repos/octo/new", headers=headers())
    assert gone.status_code == 404


async def test_a_limit_outside_1_to_100_names_the_field(harness: Harness) -> None:
    response = await harness.http.get("/v1/repos", params={"limit": 101}, headers=headers())
    assert response.status_code == 422
    assert response.json()["code"] == "invalid-request"
    assert "`query.limit`" in response.json()["detail"]


async def test_a_misspelt_field_is_named(harness: Harness) -> None:
    response = await harness.http.post(
        "/v1/repos", json={"name": "x", "visiblity": "public"}, headers=headers()
    )
    assert response.status_code == 422
    assert "visiblity" in response.json()["detail"]


async def test_pull_requests(harness: Harness) -> None:
    seed_pull(harness)
    http = harness.http
    listed = await http.get(
        f"{BASE}/pulls", params={"state": "open", "limit": 5}, headers=headers()
    )
    assert set(listed.json()) == {"pulls", "total"}
    assert set(listed.json()["pulls"][0]) == PULL_KEYS

    detail = (await http.get(f"{BASE}/pulls/42", headers=headers())).json()
    assert set(detail) == {"pull", "body", "reviews", "threads", "checks"}
    assert set(detail["pull"]) == PULL_KEYS
    assert set(detail["reviews"][0]) == {"author", "state", "body"}
    assert set(detail["threads"][0]) == {"path", "line", "author", "body", "resolved"}
    assert set(detail["checks"][0]) == CHECK_KEYS
    assert detail["checks"][0]["failing_steps"] == ["pytest"]

    opened = await http.post(
        f"{BASE}/pulls", json={"title": "t", "head": "f", "draft": True}, headers=headers()
    )
    assert opened.status_code == 201
    assert opened.json()["number"] == 43
    assert opened.json()["base"] == "main"

    closed = await http.patch(f"{BASE}/pulls/42", json={"state": "closed"}, headers=headers())
    assert closed.json()["state"] == "closed"
    nothing = await http.patch(f"{BASE}/pulls/42", json={}, headers=headers())
    assert nothing.status_code == 422


async def test_changed_files(harness: Harness) -> None:
    seed_pull(harness)
    harness.gateway.changed[(REPO, 42)] = [
        ChangedFile(path="a.py", status="modified", additions=1, deletions=1, patch="@@ -1 +1 @@"),
        ChangedFile(path="b.py", status="renamed", previous_path="old.py"),
    ]
    response = await harness.http.get(
        f"{BASE}/pulls/42/files", params={"limit": 1}, headers=headers()
    )
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 2
    assert set(body["files"][0]) == {
        "path",
        "status",
        "additions",
        "deletions",
        "patch",
        "patch_truncated",
        "previous_path",
    }
    missing = await harness.http.get(f"{BASE}/pulls/7/files", headers=headers())
    assert missing.status_code == 404
    assert missing.json()["code"] == "not-found"


async def test_merge_review_and_comment(harness: Harness) -> None:
    seed_pull(harness)
    http = harness.http
    merged = await http.post(
        f"{BASE}/pulls/42/merge",
        json={"method": "squash", "delete_branch": True},
        headers=headers(),
    )
    assert merged.json() == {"merged": True, "sha": FAKE_SHA, "message": "merged by squash"}

    reviewed = await http.post(
        f"{BASE}/pulls/42/reviews", json={"event": "approve", "body": ""}, headers=headers()
    )
    assert reviewed.status_code == 201
    assert set(reviewed.json()) == {"state", "url"}
    unreasoned = await http.post(
        f"{BASE}/pulls/42/reviews", json={"event": "request_changes"}, headers=headers()
    )
    assert unreasoned.status_code == 422

    commented = await http.post(
        f"{BASE}/issues/42/comments", json={"body": "Thanks"}, headers=headers()
    )
    assert commented.status_code == 201
    assert set(commented.json()) == {"url"}


async def test_a_merge_conflict_is_409(harness: Harness) -> None:
    harness.gateway.refuse["merge"] = GitHubConflictError("Pull Request is not mergeable")
    response = await harness.http.post(f"{BASE}/pulls/1/merge", json={}, headers=headers())
    assert response.status_code == 409
    assert response.json()["detail"] == "Pull Request is not mergeable"


async def test_issues(harness: Harness) -> None:
    harness.gateway.issue_list[(REPO, 7)] = Issue(
        repo=REPO, number=7, title="Bug", state="open", labels=["bug"]
    )
    http = harness.http
    listed = await http.get(f"{BASE}/issues", params={"state": "all"}, headers=headers())
    assert set(listed.json()) == {"issues", "total"}
    assert set(listed.json()["issues"][0]) == ISSUE_KEYS

    opened = await http.post(
        f"{BASE}/issues", json={"title": "Bug", "body": "b", "labels": ["bug"]}, headers=headers()
    )
    assert opened.status_code == 201
    assert opened.json()["number"] == 8

    closed = await http.patch(f"{BASE}/issues/7", json={"state": "closed"}, headers=headers())
    assert closed.json()["state"] == "closed"


async def test_ci_reads_and_controls(harness: Harness) -> None:
    harness.gateway.check_runs[(REPO, "pull/42")] = Checks(summary="failure", checks=[CHECK])
    harness.gateway.logs[(REPO, "991")] = "\n".join(
        f"2026-01-01T00:00:0{i}.0000000Z line {i}" for i in range(5)
    )
    http = harness.http
    checks = await http.get(f"{BASE}/checks", params={"ref": "pull/42"}, headers=headers())
    assert set(checks.json()) == {"summary", "checks"}
    assert checks.json()["summary"] == "failure"
    assert set(checks.json()["checks"][0]) == CHECK_KEYS

    log = await http.get(
        f"{BASE}/checks/991/log", params={"from": "line 3", "lines": 120}, headers=headers()
    )
    assert log.json() == {"text": "line 3\nline 4", "shown": 2, "total": 5, "truncated": True}
    tail = await http.get(f"{BASE}/checks/991/log", params={"lines": 2}, headers=headers())
    assert tail.json()["text"] == "line 3\nline 4"

    rerun = await http.post(
        f"{BASE}/checks/991/rerun", json={"failed_only": True}, headers=headers()
    )
    assert rerun.json() == {"queued": True}
    cancelled = await http.post(f"{BASE}/runs/991/cancel", headers=headers())
    assert cancelled.json() == {"cancelled": True}
    dispatched = await http.post(
        f"{BASE}/workflows/ci.yml/dispatch",
        json={"ref": "main", "inputs": {"a": "b"}},
        headers=headers(),
    )
    assert dispatched.json() == {"dispatched": True}


async def test_a_log_window_is_capped_by_the_operator(harness: Harness) -> None:
    harness.gateway.logs[(REPO, "1")] = "\n".join(str(i) for i in range(1000))
    response = await harness.http.get(
        f"{BASE}/checks/1/log", params={"lines": 100_000}, headers=headers()
    )
    assert response.json()["shown"] == harness.container.settings.log_lines_max


async def test_contents_tree_commit_and_branches(harness: Harness) -> None:
    harness.gateway.files[(REPO, "src/a.py")] = "x = 1\n"
    http = harness.http
    read = await http.get(f"{BASE}/contents", params={"path": "src/a.py"}, headers=headers())
    assert read.json() == {
        "path": "src/a.py",
        "ref": "main",
        "text": "x = 1\n",
        "shown": 6,
        "total": 6,
        "truncated": False,
        "binary": False,
    }
    tree = await http.get(f"{BASE}/tree", params={"ref": "main"}, headers=headers())
    assert tree.json() == {"entries": [{"path": "src/a.py", "type": "file", "size": 6}], "total": 1}

    committed = await http.post(
        f"{BASE}/commits",
        json={"branch": "f/x", "message": "m", "files": [{"path": "b.py", "content": "y"}]},
        headers=headers(),
    )
    assert committed.status_code == 201
    assert set(committed.json()) == {"sha", "branch", "url"}
    assert committed.json()["branch"] == "f/x"

    branch = await http.post(
        f"{BASE}/branches", json={"name": "g", "start": "main"}, headers=headers()
    )
    assert branch.json() == {"name": "g", "sha": FAKE_SHA}
    taken = await http.post(f"{BASE}/branches", json={"name": "g"}, headers=headers())
    assert taken.status_code == 409

    deleted = await http.delete(f"{BASE}/branches/f%2Fx", headers=headers())
    assert deleted.status_code == 204
    again = await http.delete(f"{BASE}/branches/f%2Fx", headers=headers())
    assert again.status_code == 404


async def test_a_commit_without_files_is_refused(harness: Harness) -> None:
    response = await harness.http.post(
        f"{BASE}/commits", json={"branch": "f", "message": "m", "files": []}, headers=headers()
    )
    assert response.status_code == 422
    assert "files" in response.json()["detail"]
