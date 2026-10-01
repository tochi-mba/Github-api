"""The real gateway against GitHub's documented payloads, served by respx.

Each payload carries fields the public models do not name, so every assertion on an answer
is also an assertion that nothing raw got through.
"""

from __future__ import annotations

import base64
import json
from typing import TYPE_CHECKING, Any

import httpx
import pytest
import respx

from github_api.github import queries
from github_api.github.client import GitHubClient
from github_api.github.errors import GitHubNotFoundError, GitHubUnprocessableError
from github_api.github.http import GitHubHttp
from github_api.github.models import GitHubCredential

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

API = "https://api.github.test"
GRAPHQL = f"{API}/graphql"
APP = GitHubCredential(headers={"Authorization": "Bearer ghu_one"}, kind="app")
PAT = GitHubCredential(headers={"Authorization": "Bearer github_pat_x"}, kind="pat")
REPO = "octo/hello"
R = f"/repos/{REPO}"
SHA = "a" * 40
EXTRA = {"node_id": "MDQ6", "_links": {"self": "x"}}

REPO_NODE = {
    "nameWithOwner": REPO,
    "isPrivate": False,
    "description": "d",
    "url": "u",
    "defaultBranchRef": {"name": "main", "target": {"statusCheckRollup": {"state": "SUCCESS"}}},
    "pullRequests": {"totalCount": 1},
    "issues": {"totalCount": 2},
    "forkCount": 9,
}
PULL_NODE = {
    "number": 42,
    "title": "Add",
    "state": "OPEN",
    "isDraft": False,
    "url": "u",
    "headRefName": "feature",
    "baseRefName": "main",
    "mergeStateStatus": "CLEAN",
    "author": {"login": "octo"},
    "repository": {"nameWithOwner": REPO},
    "commits": {"nodes": [{"commit": {"statusCheckRollup": {"state": "SUCCESS"}}}]},
}
ROLLUP = {
    "state": "FAILURE",
    "contexts": {
        "nodes": [
            {
                "__typename": "CheckRun",
                "databaseId": 991,
                "name": "tests",
                "status": "COMPLETED",
                "conclusion": "FAILURE",
                "detailsUrl": "u",
                "checkSuite": {"workflowRun": {"workflow": {"name": "CI"}}},
                "steps": {"nodes": [{"name": "pytest", "conclusion": "FAILURE"}]},
            }
        ]
    },
}


def data(**found: Any) -> dict[str, Any]:
    return {"data": found}


def sent(route: respx.Route) -> Any:
    return json.loads(route.calls.last.request.content)


@pytest.fixture
async def router() -> AsyncIterator[respx.MockRouter]:
    with respx.mock(base_url=API, assert_all_called=True) as mocked:
        yield mocked


@pytest.fixture
async def github(router: respx.MockRouter) -> AsyncIterator[GitHubClient]:
    http = GitHubHttp(base_url=API, graphql_url=GRAPHQL, timeout_seconds=5)
    found = GitHubClient(http, file_chars_max=10, tree_entries_max=2, patch_chars_max=100)
    yield found
    await found.aclose()


async def test_ready_and_visibility(router: respx.MockRouter, github: GitHubClient) -> None:
    router.get("/rate_limit").respond(200, json={})
    assert await github.ready() == (True, None)
    router.get(R).respond(200, json={"full_name": REPO})
    router.get("/repos/octo/secret").respond(404, json={"message": "Not Found"})
    assert await github.visible(APP, REPO) is True
    assert await github.visible(APP, "octo/secret") is False


async def test_me_for_a_token_counts_by_the_last_page(
    router: respx.MockRouter, github: GitHubClient
) -> None:
    router.get("/user").respond(200, json={"login": "octo", "id": 1, **EXTRA})
    repos = router.get("/user/repos").respond(
        200,
        json=[{"id": 1}],
        headers={
            "Link": f'<{API}/user/repos?per_page=1&page=2>; rel="next", '
            f'<{API}/user/repos?per_page=1&page=37>; rel="last"'
        },
    )
    identity = await github.me(PAT)
    assert identity.model_dump() == {
        "login": "octo",
        "kind": "pat",
        "selection": "",
        "repositories": 37,
    }
    assert repos.calls.last.request.url.params["per_page"] == "1"

    repos.respond(200, json=[{"id": 1}])
    assert (await github.me(PAT)).repositories == 1
    repos.respond(200, json={"weird": True})
    assert (await github.me(PAT)).repositories == 0


async def test_me_for_the_app_sums_its_installations(
    router: respx.MockRouter, github: GitHubClient
) -> None:
    router.get("/user").respond(200, json={"login": "octo"})
    installs = router.get("/user/installations").respond(
        200,
        json={
            "total_count": 2,
            "installations": [
                {"id": 1, "repository_selection": "selected", "permissions": {}},
                {"id": 2, "repository_selection": "all"},
            ],
        },
    )
    router.get("/user/installations/1/repositories").respond(200, json={"total_count": 3})
    router.get("/user/installations/2/repositories").respond(200, json={"total_count": 40})
    identity = await github.me(APP)
    assert (identity.kind, identity.selection, identity.repositories) == ("app", "all", 43)

    installs.respond(
        200,
        json={"total_count": 1, "installations": [{"id": 1, "repository_selection": "selected"}]},
    )
    assert (await github.me(APP)).selection == "selected"
    installs.respond(200, json={"total_count": 0, "installations": []})
    assert (await github.me(APP)).selection == ""


async def test_finding_repositories(router: respx.MockRouter, github: GitHubClient) -> None:
    route = router.post(GRAPHQL).respond(
        200, json=data(viewer={"repositories": {"totalCount": 135, "nodes": [REPO_NODE]}})
    )
    page = await github.find_repos(APP, query="", owner="", limit=1)
    assert sent(route)["variables"] == {"first": 1}
    assert sent(route)["query"] == queries.VIEWER_REPOS
    assert (page.total, page.repos[0].ci, page.repos[0].open_pulls) == (135, "success", 1)

    route.respond(200, json=data(search={"repositoryCount": 0, "nodes": [REPO_NODE, {}]}))
    page = await github.find_repos(APP, query="hel", owner="octo", limit=5)
    assert sent(route)["variables"] == {"q": "hel user:octo", "first": 5}
    assert (page.total, len(page.repos)) == (1, 1)
    await github.find_repos(APP, query="", owner="octo", limit=5)
    assert sent(route)["variables"]["q"] == "user:octo"


async def test_one_repository_create_update_delete(
    router: respx.MockRouter, github: GitHubClient
) -> None:
    graphql = router.post(GRAPHQL).respond(200, json=data(repository=REPO_NODE))
    repo = await github.repo(APP, REPO)
    assert sent(graphql)["variables"] == {"owner": "octo", "name": "hello"}
    assert repo.full_name == REPO

    router.get("/user").respond(200, json={"login": "Octo"})
    created = {
        **EXTRA,
        "full_name": "octo/new",
        "private": True,
        "default_branch": "main",
        "html_url": "u",
        "open_issues_count": 0,
        "owner": {"login": "octo"},
    }
    user = router.post("/user/repos").respond(201, json=created)
    org = router.post("/orgs/acme/repos").respond(201, json={**created, "full_name": "acme/new"})
    made = await github.create_repo(
        APP, name="new", owner="octo", visibility="private", description=""
    )
    assert sent(user) == {"name": "new", "description": "", "private": True}
    assert made.full_name == "octo/new"
    await github.create_repo(APP, name="new", owner="", visibility="public", description="d")
    assert sent(user)["private"] is False
    org_made = await github.create_repo(
        APP, name="new", owner="acme", visibility="internal", description=""
    )
    assert sent(org) == {"name": "new", "description": "", "visibility": "internal"}
    assert org_made.full_name == "acme/new"
    with pytest.raises(GitHubUnprocessableError, match="internal"):
        await github.create_repo(APP, name="new", owner="", visibility="internal", description="")

    patch = router.patch(R).respond(200, json={"full_name": REPO, **EXTRA})
    changed = await github.update_repo(APP, REPO, {"visibility": "public"})
    assert sent(patch) == {"visibility": "public"}
    assert changed.full_name == REPO
    patch.respond(200, json={})
    await github.update_repo(APP, REPO, {"archived": True})
    assert sent(graphql)["variables"]["name"] == "hello"

    deleted = router.delete(R).respond(204)
    await github.delete_repo(APP, REPO)
    assert deleted.called


async def test_pull_request_reads(router: respx.MockRouter, github: GitHubClient) -> None:
    graphql = router.post(GRAPHQL)
    graphql.respond(
        200, json=data(repository={"pullRequests": {"totalCount": 9, "nodes": [PULL_NODE]}})
    )
    page = await github.pulls(APP, REPO, state="closed", limit=1)
    assert sent(graphql)["variables"]["states"] == ["CLOSED", "MERGED"]
    assert (page.total, page.pulls[0].mergeable) == (9, "clean")

    detail_node = {
        **PULL_NODE,
        "body": "Why",
        "reviews": {"nodes": [{"author": {"login": "rev"}, "state": "APPROVED", "body": "ok"}]},
        "reviewThreads": {
            "nodes": [
                {
                    "isResolved": False,
                    "path": "a.py",
                    "line": 3,
                    "comments": {"nodes": [{"author": {"login": "rev"}, "body": "nit"}]},
                }
            ]
        },
        "latest": {"nodes": [{"commit": {"statusCheckRollup": ROLLUP}}]},
    }
    graphql.respond(200, json=data(repository={"pullRequest": detail_node}))
    detail = await github.pull(APP, REPO, 42)
    assert sent(graphql)["variables"] == {"owner": "octo", "name": "hello", "number": 42}
    assert detail.body == "Why"
    assert detail.reviews[0].state == "approved"
    assert detail.threads[0].body == "nit"
    assert detail.checks[0].model_dump() == {
        "id": "991",
        "name": "tests",
        "status": "completed",
        "conclusion": "failure",
        "workflow": "CI",
        "failing_steps": ["pytest"],
        "url": "u",
    }

    graphql.respond(200, json=data(repository={"pullRequest": PULL_NODE}))
    assert (await github.pull_summary(APP, REPO, 42)).checks == "success"
    graphql.respond(
        200,
        json=data(
            repository={
                "pullRequest": {
                    "reviews": {
                        "totalCount": 2,
                        "nodes": [{"author": {"login": "rev"}, "state": "COMMENTED", "body": "b"}],
                    }
                }
            }
        ),
    )
    latest = await github.latest_review(APP, REPO, 42)
    assert (latest.total, latest.latest and latest.latest.state) == (2, "commented")


async def test_changed_files(router: respx.MockRouter, github: GitHubClient) -> None:
    router.get(f"{R}/pulls/42").respond(200, json={"changed_files": 30, "number": 42})
    files = router.get(f"{R}/pulls/42/files").respond(
        200,
        json=[
            {
                "sha": "s",
                "filename": "a.py",
                "status": "modified",
                "additions": 1,
                "deletions": 1,
                "changes": 2,
                "patch": "@@ -1 +1 @@",
                "blob_url": "x",
            },
            {"filename": "img.png", "status": "added", "additions": 0, "deletions": 0},
            "junk",
        ],
    )
    page = await github.changes(APP, REPO, 42, limit=2)
    assert files.calls.last.request.url.params["per_page"] == "2"
    assert page.total == 30
    assert [one.path for one in page.files] == ["a.py", "img.png"]
    assert page.files[1].patch_truncated is True
    files.respond(200, json={"message": "odd"})
    assert (await github.changes(APP, REPO, 42, limit=2)).files == []


def graphql_answer(request: httpx.Request) -> httpx.Response:
    if json.loads(request.content)["query"].lstrip().startswith("mutation"):
        return httpx.Response(200, json=data(done={"clientMutationId": None}))
    return httpx.Response(200, json=data(repository={"pullRequest": PULL_NODE}))


async def test_pull_request_writes(router: respx.MockRouter, github: GitHubClient) -> None:
    graphql = router.post(GRAPHQL).mock(side_effect=graphql_answer)
    router.get(R).respond(200, json={"default_branch": "trunk"})
    opened = router.post(f"{R}/pulls").respond(201, json={"number": 42, **EXTRA})
    pull = await github.open_pull(APP, REPO, {"title": "t", "head": "f", "base": ""})
    assert sent(opened)["base"] == "trunk"
    assert pull.number == 42
    await github.open_pull(APP, REPO, {"title": "t", "head": "f", "base": "dev"})
    assert sent(opened)["base"] == "dev"

    patched = router.patch(f"{R}/pulls/42").respond(200, json={"node_id": "PR_1"})
    fetched = router.get(f"{R}/pulls/42").respond(200, json={"node_id": "PR_2"})
    await github.update_pull(APP, REPO, 42, {"title": "New"})
    assert sent(patched) == {"title": "New"}
    await github.update_pull(APP, REPO, 42, {"state": "open", "draft": False})
    assert json.loads(graphql.calls[-2].request.content) == {
        "query": queries.READY_FOR_REVIEW,
        "variables": {"id": "PR_1"},
    }
    calls = len(fetched.calls)
    await github.update_pull(APP, REPO, 42, {"draft": True})
    assert len(fetched.calls) == calls + 1
    assert json.loads(graphql.calls[-2].request.content)["query"] == queries.TO_DRAFT


async def test_merge_and_delete_the_head_branch(
    router: respx.MockRouter, github: GitHubClient
) -> None:
    merge = router.put(f"{R}/pulls/42/merge").respond(
        200, json={"merged": True, "sha": SHA, "message": "Pull Request successfully merged"}
    )
    head = {"ref": "feature", "repo": {"full_name": REPO}}
    pull = router.get(f"{R}/pulls/42").respond(200, json={"head": head})
    ref = router.delete(f"{R}/git/refs/heads/feature").respond(204)
    merged = await github.merge(APP, REPO, 42, method="squash", delete_branch=True)
    assert sent(merge) == {"merge_method": "squash"}
    assert merged.model_dump() == {
        "merged": True,
        "sha": SHA,
        "message": "Pull Request successfully merged",
    }
    assert ref.call_count == 1

    ref.respond(422, json={"message": "Reference does not exist"})
    await github.merge(APP, REPO, 42, method="merge", delete_branch=True)

    pull.respond(200, json={"head": {"ref": "feature", "repo": {"full_name": "fork/hello"}}})
    await github.merge(APP, REPO, 42, method="rebase", delete_branch=True)
    assert ref.call_count == 2

    merge.respond(200, json={"merged": False, "message": "no"})
    await github.merge(APP, REPO, 42, method="squash", delete_branch=True)
    await github.merge(APP, REPO, 42, method="squash", delete_branch=False)
    assert pull.call_count == 3


async def test_reviews_and_comments(router: respx.MockRouter, github: GitHubClient) -> None:
    review = router.post(f"{R}/pulls/42/reviews").respond(
        200, json={"id": 1, "state": "APPROVED", "html_url": "u", "user": {}}
    )
    assert (await github.review(APP, REPO, 42, event="approve", body="")).state == "approved"
    assert sent(review) == {"event": "APPROVE"}
    await github.review(APP, REPO, 42, event="request_changes", body="fix")
    assert sent(review) == {"event": "REQUEST_CHANGES", "body": "fix"}
    comment = router.post(f"{R}/issues/42/comments").respond(201, json={"html_url": "c", "id": 9})
    assert (await github.comment(APP, REPO, 42, "Thanks")).url == "c"
    assert sent(comment) == {"body": "Thanks"}


async def test_issues(router: respx.MockRouter, github: GitHubClient) -> None:
    node = {
        "number": 7,
        "title": "Bug",
        "state": "OPEN",
        "url": "u",
        "author": {"login": "o"},
        "repository": {"nameWithOwner": REPO},
        "labels": {"nodes": [{"name": "bug"}]},
    }
    graphql = router.post(GRAPHQL).respond(
        200, json=data(repository={"issues": {"totalCount": 0, "nodes": [node]}})
    )
    page = await github.issues(APP, REPO, state="all", limit=5)
    assert sent(graphql)["variables"]["states"] is None
    assert (page.total, page.issues[0].labels) == (1, ["bug"])

    rest = {
        "number": 7,
        "title": "Bug",
        "state": "open",
        "user": {"login": "o"},
        "labels": [{"name": "bug"}],
        "html_url": "u",
        **EXTRA,
    }
    opened = router.post(f"{R}/issues").respond(201, json=rest)
    issue = await github.open_issue(APP, REPO, title="Bug", body="b", labels=["bug"])
    assert sent(opened) == {"title": "Bug", "body": "b", "labels": ["bug"]}
    assert issue.repo == REPO
    closed = router.patch(f"{R}/issues/7").respond(200, json={**rest, "state": "closed"})
    assert (await github.set_issue_state(APP, REPO, 7, "closed")).state == "closed"
    assert sent(closed) == {"state": "closed"}


async def test_checks_for_a_pull_request_and_for_a_ref(
    router: respx.MockRouter, github: GitHubClient
) -> None:
    graphql = router.post(GRAPHQL).respond(
        200,
        json=data(
            repository={
                "pullRequest": {"commits": {"nodes": [{"commit": {"statusCheckRollup": ROLLUP}}]}}
            }
        ),
    )
    found = await github.checks(APP, REPO, "pull/42")
    assert sent(graphql)["query"] == queries.PULL_CHECKS
    assert (found.summary, found.checks[0].id) == ("failure", "991")

    graphql.respond(200, json=data(repository={"object": {"statusCheckRollup": None}}))
    found = await github.checks(APP, REPO, "main")
    assert sent(graphql)["variables"]["ref"] == "main"
    assert (found.summary, found.checks) == ("none", [])
    graphql.respond(200, json=data(repository={"object": None}))
    with pytest.raises(GitHubNotFoundError):
        await github.checks(APP, REPO, "no-such-branch")


async def test_actions(router: respx.MockRouter, github: GitHubClient) -> None:
    router.get(f"{R}/actions/jobs/991/logs").respond(200, text="log text")
    assert await github.raw_log(APP, REPO, "991") == "log text"

    job = router.get(f"{R}/actions/jobs/991").respond(
        200,
        json={
            "id": 991,
            "run_id": 55,
            "name": "tests",
            "status": "completed",
            "conclusion": "success",
            "html_url": "u",
            "steps": [],
        },
    )
    status = await github.run_status(APP, REPO, "991")
    assert (status.id, status.status, status.conclusion) == ("991", "completed", "success")

    rerun_job = router.post(f"{R}/actions/jobs/991/rerun").respond(201)
    rerun_failed = router.post(f"{R}/actions/runs/55/rerun-failed-jobs").respond(201)
    assert (await github.rerun(APP, REPO, "991", failed_only=False)).queued is True
    assert (await github.rerun(APP, REPO, "991", failed_only=True)).queued is True
    assert rerun_job.called
    assert rerun_failed.called

    cancel = router.post(f"{R}/actions/runs/55/cancel").respond(202)
    assert (await github.cancel_run(APP, REPO, "991")).cancelled is True
    assert cancel.called

    router.get(f"{R}/actions/jobs/77").respond(404, json={"message": "Not Found"})
    router.get(f"{R}/actions/runs/77").respond(
        200, json={"id": 77, "name": "CI", "status": "queued", "conclusion": None}
    )
    assert (await github.run_status(APP, REPO, "77")).name == "CI"
    rerun_run = router.post(f"{R}/actions/runs/77/rerun").respond(201)
    await github.rerun(APP, REPO, "77", failed_only=False)
    assert rerun_run.called
    assert job.called

    dispatch = router.post(f"{R}/actions/workflows/ci.yml/dispatches").respond(204)
    assert (await github.dispatch(APP, REPO, "ci.yml", ref="main", inputs={"a": "b"})).dispatched
    assert sent(dispatch) == {"ref": "main", "inputs": {"a": "b"}}


def encoded(content: bytes) -> str:
    return base64.b64encode(content).decode()


async def test_reading_a_file(router: respx.MockRouter, github: GitHubClient) -> None:
    contents = router.get(f"{R}/contents/src/a.py").respond(
        200,
        json={
            "type": "file",
            "path": "src/a.py",
            "encoding": "base64",
            "sha": "s",
            "content": encoded(b"0123456789abcdef"),
            **EXTRA,
        },
    )
    found = await github.read_file(APP, REPO, "/src/a.py", ref="dev")
    assert contents.calls.last.request.url.params["ref"] == "dev"
    assert found.model_dump() == {
        "path": "src/a.py",
        "ref": "dev",
        "text": "0123456789",
        "shown": 10,
        "total": 16,
        "truncated": True,
        "binary": False,
    }

    router.get(R).respond(200, json={"default_branch": "main"})
    contents.respond(
        200,
        json={
            "type": "file",
            "path": "src/a.py",
            "encoding": "base64",
            "content": encoded(b"\x00\x01"),
        },
    )
    binary = await github.read_file(APP, REPO, "src/a.py", ref="")
    assert (binary.ref, binary.binary, binary.text, binary.total) == ("main", True, "", 0)
    contents.respond(
        200,
        json={
            "type": "file",
            "path": "src/a.py",
            "encoding": "base64",
            "content": encoded(b"\xff\xfe"),
        },
    )
    assert (await github.read_file(APP, REPO, "src/a.py", ref="x")).binary is True

    big = router.get(f"{R}/contents/big.txt")
    big.side_effect = [
        respx.MockResponse(
            200, json={"type": "file", "path": "big.txt", "encoding": "none", "content": ""}
        ),
        respx.MockResponse(200, content=b"short"),
    ]
    raw = await github.read_file(APP, REPO, "big.txt", ref="x")
    assert raw.text == "short"
    assert big.calls.last.request.headers["Accept"] == "application/vnd.github.raw"


@pytest.mark.parametrize(
    "payload", [[{"type": "file", "path": "a"}], {"type": "symlink", "path": "l"}]
)
async def test_a_directory_or_link_is_not_a_file(
    router: respx.MockRouter, github: GitHubClient, payload: Any
) -> None:
    router.get(f"{R}/contents/src").respond(200, json=payload)
    with pytest.raises(GitHubUnprocessableError, match="not a file"):
        await github.read_file(APP, REPO, "src", ref="x")


async def test_listing_a_tree(router: respx.MockRouter, github: GitHubClient) -> None:
    tree = router.get(f"{R}/git/trees/HEAD").respond(
        200,
        json={
            "sha": "t",
            "truncated": False,
            "tree": [
                {"path": "README.md", "type": "blob", "size": 5, "sha": "1", "mode": "100644"},
                {"path": "src", "type": "tree", "sha": "2"},
                {"path": "src/a.py", "type": "blob", "size": 3},
                {"path": "src/b.py", "type": "blob", "size": 4},
                {"path": "src/c.py", "type": "blob", "size": 6},
            ],
        },
    )
    found = await github.tree(APP, REPO, "src/", ref="")
    assert tree.calls.last.request.url.params["recursive"] == "1"
    assert found.total == 3
    assert [entry.path for entry in found.entries] == ["src/a.py", "src/b.py"]
    whole = await github.tree(APP, REPO, "", ref="")
    assert whole.total == 5
    with pytest.raises(GitHubNotFoundError, match="no directory `docs`"):
        await github.tree(APP, REPO, "docs", ref="")
    router.get(f"{R}/git/trees/v1.0").respond(200, json={"tree": []})
    assert (await github.tree(APP, REPO, "", ref="v1.0")).total == 0


def git_routes(router: respx.MockRouter) -> dict[str, respx.Route]:
    return {
        "commit": router.get(f"{R}/git/commits/{SHA}").respond(
            200, json={"sha": SHA, "tree": {"sha": "t0"}}
        ),
        "tree": router.post(f"{R}/git/trees").respond(201, json={"sha": "t1", "tree": []}),
        "new": router.post(f"{R}/git/commits").respond(
            201, json={"sha": "c1", "html_url": "u", **EXTRA}
        ),
    }


async def test_committing_to_an_existing_branch(
    router: respx.MockRouter, github: GitHubClient
) -> None:
    routes = git_routes(router)
    router.get(f"{R}/git/ref/heads/f/x").respond(200, json={"object": {"sha": SHA}})
    moved = router.patch(f"{R}/git/refs/heads/f/x").respond(200, json={})
    committed = await github.commit(
        APP, REPO, branch="f/x", message="m", files=[{"path": "a.py", "content": "y"}], base=""
    )
    assert committed.model_dump() == {"sha": "c1", "branch": "f/x", "url": "u"}
    assert sent(routes["tree"]) == {
        "base_tree": "t0",
        "tree": [{"path": "a.py", "mode": "100644", "type": "blob", "content": "y"}],
    }
    assert sent(routes["new"]) == {"message": "m", "tree": "t1", "parents": [SHA]}
    assert sent(moved) == {"sha": "c1", "force": False}


async def test_committing_to_a_new_branch_from_a_base_or_the_default(
    router: respx.MockRouter, github: GitHubClient
) -> None:
    git_routes(router)
    router.get(f"{R}/git/ref/heads/new").respond(404, json={"message": "Not Found"})
    router.get(f"{R}/git/ref/heads/dev").respond(200, json={"object": {"sha": SHA}})
    created = router.post(f"{R}/git/refs").respond(201, json={})
    await github.commit(APP, REPO, branch="new", message="m", files=[], base="dev")
    assert sent(created) == {"ref": "refs/heads/new", "sha": "c1"}

    router.get(R).respond(200, json={"default_branch": "dev"})
    await github.commit(APP, REPO, branch="new", message="m", files=[], base="")
    assert created.call_count == 2


async def test_branches(router: respx.MockRouter, github: GitHubClient) -> None:
    created = router.post(f"{R}/git/refs").respond(201, json={"ref": "refs/heads/g"})
    branch = await github.create_branch(APP, REPO, "g", start=SHA)
    assert branch.model_dump() == {"name": "g", "sha": SHA}
    assert sent(created) == {"ref": "refs/heads/g", "sha": SHA}

    router.get(f"{R}/git/ref/heads/main").respond(200, json={"object": {"sha": SHA}})
    assert (await github.create_branch(APP, REPO, "h", start="main")).sha == SHA
    router.get(f"{R}/git/ref/heads/gone").respond(404, json={})
    with pytest.raises(GitHubNotFoundError, match="no branch `gone`"):
        await github.create_branch(APP, REPO, "i", start="gone")

    deleted = router.delete(f"{R}/git/refs/heads/f/x").respond(204)
    await github.delete_branch(APP, REPO, "f/x")
    assert deleted.called
