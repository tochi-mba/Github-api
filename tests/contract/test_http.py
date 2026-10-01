"""GitHub's wire, as respx serves it: statuses, rate limits, GraphQL errors, downloads."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import httpx
import pytest
import respx

from github_api.github.errors import (
    NOT_FOUND,
    GitHubConflictError,
    GitHubForbiddenError,
    GitHubNotFoundError,
    GitHubRateLimitedError,
    GitHubUnauthorizedError,
    GitHubUnavailableError,
    GitHubUnprocessableError,
)
from github_api.github.http import GitHubHttp
from github_api.github.models import GitHubCredential

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

API = "https://api.github.test"
GRAPHQL = f"{API}/graphql"
CRED = GitHubCredential(headers={"Authorization": "Bearer ghu_one"}, kind="app")
OTHER = GitHubCredential(headers={"Authorization": "Bearer ghu_two"}, kind="app")
NOW = 1_700_000_000.0


class Clock:
    now = NOW

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
async def router() -> AsyncIterator[respx.MockRouter]:
    with respx.mock(base_url=API, assert_all_called=False) as mocked:
        yield mocked


@pytest.fixture
async def http(router: respx.MockRouter, clock: Clock) -> AsyncIterator[GitHubHttp]:
    found = GitHubHttp(base_url=API, graphql_url=GRAPHQL, timeout_seconds=5, clock=clock)
    yield found
    await found.aclose()


async def test_the_credential_and_api_version_are_attached(
    router: respx.MockRouter, http: GitHubHttp
) -> None:
    route = router.get("/user").respond(200, json={"login": "octo"})
    assert await http.json("GET", "/user", cred=CRED) == {"login": "octo"}
    sent = route.calls.last.request
    assert sent.headers["Authorization"] == "Bearer ghu_one"
    assert sent.headers["X-GitHub-Api-Version"] == "2022-11-28"
    assert sent.headers["Accept"] == "application/vnd.github+json"


async def test_an_empty_answer_is_none(router: respx.MockRouter, http: GitHubHttp) -> None:
    router.delete("/x").respond(204)
    router.post("/y").respond(201)
    assert await http.json("DELETE", "/x", cred=CRED) is None
    assert await http.json("POST", "/y", cred=CRED) is None


async def test_a_spent_bucket_is_refused_here_until_github_resets_it(
    router: respx.MockRouter, http: GitHubHttp, clock: Clock
) -> None:
    route = router.get("/user").respond(
        403,
        json={"message": "API rate limit exceeded"},
        headers={"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": str(int(NOW + 90))},
    )
    with pytest.raises(GitHubRateLimitedError) as caught:
        await http.json("GET", "/user", cred=CRED)
    assert caught.value.retry_after == 90

    clock.now += 30
    with pytest.raises(GitHubRateLimitedError) as again:
        await http.json("GET", "/user", cred=CRED)
    assert again.value.retry_after == 60
    assert route.call_count == 1

    other = router.get("/other").respond(200, json={})
    await http.json("GET", "/other", cred=OTHER)
    assert other.call_count == 1

    clock.now += 61
    route.respond(200, json={"login": "octo"})
    assert await http.json("GET", "/user", cred=CRED) == {"login": "octo"}


async def test_a_successful_last_request_also_spends_the_bucket(
    router: respx.MockRouter, http: GitHubHttp
) -> None:
    router.get("/a").respond(
        200, json={}, headers={"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "nonsense"}
    )
    await http.json("GET", "/a", cred=CRED)
    router.get("/b").respond(
        200,
        json={},
        headers={"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": str(int(NOW + 10))},
    )
    await http.json("GET", "/b", cred=CRED)
    with pytest.raises(GitHubRateLimitedError):
        await http.json("GET", "/a", cred=CRED)


@pytest.mark.parametrize(
    ("status", "headers", "message", "retry_after"),
    [
        (429, {"Retry-After": "7"}, "", 7),
        (403, {"Retry-After": "3"}, "You have exceeded a secondary rate limit", 3),
        (403, {}, "You have exceeded a secondary rate limit", 60),
        (403, {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": str(int(NOW - 5))}, "", 1),
    ],
)
async def test_every_shape_of_rate_limit_is_one_error(
    router: respx.MockRouter,
    http: GitHubHttp,
    status: int,
    headers: dict[str, str],
    message: str,
    retry_after: int,
) -> None:
    router.get("/x").respond(status, json={"message": message}, headers=headers)
    with pytest.raises(GitHubRateLimitedError) as caught:
        await http.json("GET", "/x", cred=CRED)
    assert caught.value.retry_after == retry_after


@pytest.mark.parametrize(
    ("status", "body", "error", "message"),
    [
        (401, {"message": "Bad credentials"}, GitHubUnauthorizedError, "stored credential"),
        (403, {"message": "Resource not accessible"}, GitHubForbiddenError, "not accessible"),
        (403, {}, GitHubForbiddenError, "refused this for the connection"),
        (404, {"message": "Not Found"}, GitHubNotFoundError, NOT_FOUND),
        (410, {"message": "Issues are disabled"}, GitHubNotFoundError, NOT_FOUND),
        (409, {"message": "Merge conflict"}, GitHubConflictError, "Merge conflict"),
        (405, {"message": "Pull Request is not mergeable"}, GitHubConflictError, "mergeable"),
        (
            422,
            {"message": "Reference already exists"},
            GitHubConflictError,
            "already exists",
        ),
        (422, {"message": "Update is not a fast forward"}, GitHubConflictError, "fast forward"),
        (422, {"message": "Reference does not exist"}, GitHubNotFoundError, "does not exist"),
        (
            422,
            {
                "message": "Validation Failed",
                "errors": [
                    {"resource": "PullRequest", "field": "head", "code": "invalid"},
                    {"message": "No commits between main and f"},
                    {"field": "base"},
                    "a plain string",
                    7,
                ],
            },
            GitHubUnprocessableError,
            "Validation Failed: `head` is invalid: No commits between main and f: "
            "`base` is invalid: a plain string",
        ),
        (422, [], GitHubUnprocessableError, "refused the request as sent"),
        (418, {"message": ""}, GitHubUnprocessableError, "GitHub answered 418"),
        (502, {"message": "Server Error"}, GitHubUnavailableError, "GitHub answered 502"),
    ],
)
async def test_statuses_become_named_errors(
    router: respx.MockRouter, http: GitHubHttp, status: int, body: Any, error: type, message: str
) -> None:
    router.get("/x").respond(status, json=body)
    with pytest.raises(error) as caught:
        await http.json("GET", "/x", cred=CRED)
    assert message in str(caught.value)


async def test_a_non_json_error_body_is_still_an_error(
    router: respx.MockRouter, http: GitHubHttp
) -> None:
    router.get("/x").respond(500, text="<html>oops</html>")
    with pytest.raises(GitHubUnavailableError):
        await http.json("GET", "/x", cred=CRED)


async def test_transport_failures_and_unreadable_answers_are_unavailable(
    router: respx.MockRouter, http: GitHubHttp
) -> None:
    router.get("/slow").mock(side_effect=httpx.ReadTimeout("slow"))
    router.get("/down").mock(side_effect=httpx.ConnectError("down"))
    router.get("/junk").respond(200, text="not json")
    with pytest.raises(GitHubUnavailableError, match="within 5s"):
        await http.json("GET", "/slow", cred=CRED)
    with pytest.raises(GitHubUnavailableError, match="could not be reached"):
        await http.json("GET", "/down", cred=CRED)
    with pytest.raises(GitHubUnavailableError, match="could not be read"):
        await http.json("GET", "/junk", cred=CRED)


async def test_graphql_returns_the_object_at_the_root(
    router: respx.MockRouter, http: GitHubHttp
) -> None:
    route = router.post(GRAPHQL).respond(
        200, json={"data": {"repository": {"pullRequest": {"number": 1}}}}
    )
    found = await http.graphql(CRED, "query", {"n": 1}, root=("repository", "pullRequest"))
    assert found == {"number": 1}
    assert route.calls.last.request.headers["Authorization"] == "Bearer ghu_one"


@pytest.mark.parametrize(
    ("document", "error"),
    [
        (
            {"data": {"repository": None}, "errors": [{"type": "NOT_FOUND", "message": "x"}]},
            GitHubNotFoundError,
        ),
        ({"data": {"repository": None}}, GitHubNotFoundError),
        (
            {"data": None, "errors": [{"type": "FORBIDDEN", "message": "nope"}]},
            GitHubForbiddenError,
        ),
        ({"data": None, "errors": [{"type": "FORBIDDEN"}]}, GitHubForbiddenError),
        ({"errors": [{"type": "RATE_LIMITED", "message": "slow"}]}, GitHubRateLimitedError),
        (
            {"errors": [{"type": "MAX_NODE_LIMIT_EXCEEDED", "message": "big"}]},
            GitHubUnprocessableError,
        ),
        ({"errors": [{"type": "SOMETHING"}]}, GitHubUnprocessableError),
        ({"errors": ["weird"]}, GitHubNotFoundError),
        ([], GitHubNotFoundError),
    ],
)
async def test_graphql_errors_become_named_errors(
    router: respx.MockRouter, http: GitHubHttp, document: Any, error: type
) -> None:
    router.post(GRAPHQL).respond(200, json=document)
    with pytest.raises(error):
        await http.graphql(CRED, "query", {}, root=("repository",))


async def test_graphql_http_failures_and_its_own_bucket(
    router: respx.MockRouter, http: GitHubHttp
) -> None:
    router.post(GRAPHQL).respond(401, json={"message": "Bad credentials"})
    with pytest.raises(GitHubUnauthorizedError):
        await http.graphql(CRED, "query", {}, root=("viewer",))
    router.post(GRAPHQL).respond(
        200,
        json={"data": {"viewer": {"login": "octo"}}},
        headers={"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": str(int(NOW + 30))},
    )
    await http.graphql(CRED, "query", {}, root=("viewer",))
    with pytest.raises(GitHubRateLimitedError):
        await http.graphql(CRED, "query", {}, root=("viewer",))
    router.get("/user").respond(200, json={})
    await http.json("GET", "/user", cred=CRED)


async def test_a_download_follows_the_redirect_without_the_credential(
    router: respx.MockRouter, http: GitHubHttp
) -> None:
    router.get("/logs").respond(302, headers={"Location": "https://blobs.test/log.txt"})
    blob = router.route(host="blobs.test", path="/log.txt").respond(200, text="line 1\nline 2")
    assert await http.download("/logs", cred=CRED) == "line 1\nline 2"
    assert "Authorization" not in blob.calls.last.request.headers

    router.get("/direct").respond(200, text="inline")
    assert await http.download("/direct", cred=CRED) == "inline"


async def test_a_download_that_storage_refuses_is_unavailable(
    router: respx.MockRouter, http: GitHubHttp
) -> None:
    router.get("/logs").respond(302, headers={"Location": "https://blobs.test/gone"})
    router.route(host="blobs.test", path="/gone").respond(403)
    with pytest.raises(GitHubUnavailableError, match="storage answered 403"):
        await http.download("/logs", cred=CRED)
    router.get("/logs2").respond(302, headers={"Location": "https://blobs.test/down"})
    router.route(host="blobs.test", path="/down").mock(side_effect=httpx.ConnectError("x"))
    with pytest.raises(GitHubUnavailableError, match="could not be reached"):
        await http.download("/logs2", cred=CRED)


async def test_ready_asks_rate_limit_without_a_credential(
    router: respx.MockRouter, http: GitHubHttp
) -> None:
    route = router.get("/rate_limit").respond(200, json={"resources": {}})
    assert await http.ready() == (True, None)
    assert "Authorization" not in route.calls.last.request.headers
    route.respond(503)
    assert await http.ready() == (False, "GitHub answered 503")
    route.mock(side_effect=httpx.ConnectError("down"))
    assert await http.ready() == (False, "ConnectError")


async def test_an_anonymous_request_has_its_own_bucket(
    router: respx.MockRouter, http: GitHubHttp
) -> None:
    router.get("/meta").respond(
        200, json={}, headers={"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": str(int(NOW + 5))}
    )
    await http.json("GET", "/meta", cred=None)
    with pytest.raises(GitHubRateLimitedError):
        await http.json("GET", "/meta", cred=None)
    await http.json("GET", "/meta", cred=CRED)
