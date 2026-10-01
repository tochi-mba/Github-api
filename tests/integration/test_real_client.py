"""The whole stack -- ASGI, keyring, the real GitHub client -- with GitHub served by respx."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
import respx
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient
from keyring_client.testing import FakeKeyring

from github_api.api.app import create_app
from tests.conftest import APP_TOKEN, build_settings, connect, headers

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

API = "https://api.github.test"
RESET = "4102444800"  # 2100-01-01: far enough that the bucket stays spent


@pytest.fixture
async def stack(keyring: FakeKeyring) -> AsyncIterator[tuple[AsyncClient, respx.MockRouter]]:
    settings = build_settings(github_base_url=API, github_graphql_url=f"{API}/graphql")
    with respx.mock(base_url=API, assert_all_called=False) as github:
        app = create_app(settings, keyring_transport=keyring.transport())
        async with (
            LifespanManager(app),
            AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http,
        ):
            yield http, github


async def test_a_401_from_github_refreshes_once_then_reports_reconnect(
    stack: tuple[AsyncClient, respx.MockRouter], keyring: FakeKeyring
) -> None:
    http, github = stack
    user = github.get("/user").respond(401, json={"message": "Bad credentials"})
    connect(keyring, "Bearer ghu_first")
    response = await http.get("/v1/me", headers=headers())
    assert response.status_code == 502
    assert response.json()["code"] == "credential-unavailable"
    assert [call.request.headers["Authorization"] for call in user.calls] == [APP_TOKEN] * 2
    assert len(keyring.internal_calls) == 2


async def test_a_spent_rate_limit_is_429_and_github_is_not_asked_again(
    stack: tuple[AsyncClient, respx.MockRouter],
) -> None:
    http, github = stack
    graphql = github.post("/graphql").respond(
        403,
        json={"message": "API rate limit exceeded"},
        headers={"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": RESET},
    )
    first = await http.get("/v1/repos/octo/hello", headers=headers())
    second = await http.get("/v1/repos/octo/hello", headers=headers())
    assert first.status_code == second.status_code == 429
    assert int(second.headers["Retry-After"]) > 0
    assert graphql.call_count == 1


async def test_a_403_on_a_repository_github_hides_is_a_404(
    stack: tuple[AsyncClient, respx.MockRouter],
) -> None:
    http, github = stack
    github.post("/repos/octo/secret/pulls").respond(
        403, json={"message": "Resource not accessible by integration"}
    )
    github.get("/repos/octo/secret").respond(404, json={"message": "Not Found"})
    github.post("/repos/octo/gone/pulls").respond(404, json={"message": "Not Found"})
    pull = {"title": "t", "head": "f", "base": "main"}
    hidden = await http.post("/v1/repos/octo/secret/pulls", json=pull, headers=headers())
    missing = await http.post("/v1/repos/octo/gone/pulls", json=pull, headers=headers())
    assert hidden.status_code == missing.status_code == 404
    assert hidden.json() == missing.json()
