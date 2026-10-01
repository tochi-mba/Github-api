"""Through the ASGI app: who may call, which credential they get, and the probes."""

from __future__ import annotations

import httpx
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient
from keyring_client.testing import FakeKeyring

from github_api.api.app import create_app
from github_api.core.config import Settings
from github_api.github.errors import (
    GitHubForbiddenError,
    GitHubRateLimitedError,
    GitHubUnavailableError,
)
from github_api.github.fake import FakeGateway
from tests.conftest import (
    APP_TOKEN,
    OTHER_ACCOUNT,
    REPO,
    Harness,
    build_settings,
    connect,
    headers,
)

PROBLEM = "application/problem+json"


async def test_healthy_never_fails(client: AsyncClient) -> None:
    response = await client.get("/healthy")
    assert response.status_code == 200
    assert response.json()["status"] == "alive"


async def test_ready_checks_keyring_github_and_the_database(client: AsyncClient) -> None:
    response = await client.get("/ready")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert set(body["checks"]) == {"keyring", "github", "database"}


async def test_ready_degrades_when_github_or_the_database_is_down(harness: Harness) -> None:
    harness.gateway.healthy = (False, "GitHub answered 503")
    response = await harness.http.get("/ready")
    assert response.status_code == 503
    assert response.json()["checks"]["github"]["detail"]["reason"] == "GitHub answered 503"

    harness.gateway.healthy = (True, None)
    harness.container.store.close()
    response = await harness.http.get("/ready")
    assert response.status_code == 503
    assert response.json()["checks"]["database"]["status"] == "degraded"


async def test_ready_degrades_when_keyring_keys_are_unreachable(
    settings: Settings, keyring: FakeKeyring, gateway: FakeGateway
) -> None:
    keyring.error = httpx.ConnectError("down")
    app = create_app(settings, keyring_transport=keyring.transport(), gateway=gateway)
    async with (
        LifespanManager(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http,
    ):
        response = await http.get("/ready")
        assert response.status_code == 503
        refused = await http.get("/v1/me", headers=headers())
    assert refused.status_code == 503
    assert refused.json()["code"] == "keyring-unavailable"


async def test_a_token_is_required(client: AsyncClient) -> None:
    response = await client.get("/v1/me")
    assert response.status_code == 401
    assert response.headers["content-type"] == PROBLEM
    assert response.headers["WWW-Authenticate"] == "Bearer"
    assert response.json()["code"] == "unauthenticated"


async def test_a_token_for_another_audience_is_refused(client: AsyncClient) -> None:
    response = await client.get("/v1/me", headers=headers(audience="spotify-api"))
    assert response.status_code == 401


async def test_me_answers_with_the_connected_identity(harness: Harness) -> None:
    response = await harness.http.get("/v1/me", headers=headers())
    assert response.status_code == 200
    assert response.json() == {
        "login": "octo",
        "kind": "app",
        "selection": "selected",
        "repositories": 1,
    }
    assert harness.gateway.calls == [("me", APP_TOKEN)]


async def test_the_profile_header_chooses_the_credential(harness: Harness) -> None:
    connect(harness.keyring, "Bearer github_pat_personal", profile="personal")
    await harness.http.get("/v1/me", headers=headers(profile="personal"))
    await harness.http.get("/v1/me", headers=headers())
    assert [auth for _, auth in harness.gateway.calls] == ["Bearer github_pat_personal", APP_TOKEN]


async def test_no_profile_header_means_the_default_profile(harness: Harness) -> None:
    connect(harness.keyring, "Bearer ghu_default", profile="default")
    response = await harness.http.get("/v1/me", headers=headers(profile=None))
    assert response.status_code == 200
    assert harness.gateway.calls == [("me", "Bearer ghu_default")]


async def test_a_malformed_profile_is_a_422_naming_the_header(client: AsyncClient) -> None:
    response = await client.get("/v1/me", headers=headers(profile="../etc"))
    assert response.status_code == 422
    assert "X-Keyring-Profile" in response.json()["detail"]


async def test_a_missing_connection_is_502_credential_missing(client: AsyncClient) -> None:
    response = await client.get("/v1/me", headers=headers(OTHER_ACCOUNT))
    assert response.status_code == 502
    assert response.json()["code"] == "credential-missing"


async def test_a_sealed_vault_is_502_credential_unavailable(harness: Harness) -> None:
    harness.keyring.sealed = True
    response = await harness.http.get("/v1/me", headers=headers())
    assert response.status_code == 502
    assert response.json()["code"] == "credential-unavailable"


async def test_a_wrong_service_token_is_the_operators_problem(
    gateway: FakeGateway, keyring: FakeKeyring
) -> None:
    settings = build_settings(keyring_service_token="x" * 40)
    app = create_app(settings, keyring_transport=keyring.transport(), gateway=gateway)
    async with (
        LifespanManager(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http,
    ):
        response = await http.get("/v1/me", headers=headers())
    assert response.status_code == 502
    assert response.json()["code"] == "keyring-rejected"


async def test_a_refused_credential_is_refreshed_once(harness: Harness) -> None:
    await harness.http.get("/v1/me", headers=headers())
    harness.gateway.rejected.add(APP_TOKEN)
    connect(harness.keyring, "Bearer ghu_second")
    response = await harness.http.get("/v1/me", headers=headers())
    assert response.status_code == 200
    assert [auth for _, auth in harness.gateway.calls] == [
        APP_TOKEN,
        APP_TOKEN,
        "Bearer ghu_second",
    ]


async def test_a_credential_refused_twice_is_502_and_forgotten(harness: Harness) -> None:
    harness.gateway.rejected.add(APP_TOKEN)
    response = await harness.http.get("/v1/me", headers=headers())
    assert response.status_code == 502
    assert response.json()["code"] == "credential-unavailable"
    assert len(harness.gateway.calls) == 2
    internal = len(harness.keyring.internal_calls)
    harness.gateway.rejected.clear()
    assert (await harness.http.get("/v1/me", headers=headers())).status_code == 200
    assert len(harness.keyring.internal_calls) == internal + 1


async def test_rate_limits_are_429_with_retry_after(harness: Harness) -> None:
    harness.gateway.refuse["repo"] = GitHubRateLimitedError("slow down", 42)
    response = await harness.http.get(f"/v1/repos/{REPO}", headers=headers())
    assert response.status_code == 429
    assert response.headers["Retry-After"] == "42"
    assert response.json()["code"] == "rate-limited"


async def test_a_repository_the_token_cannot_see_is_404_never_403(harness: Harness) -> None:
    harness.gateway.refuse["pull"] = GitHubForbiddenError("Resource not accessible")
    harness.gateway.hidden.add(REPO)
    hidden = await harness.http.get(f"/v1/repos/{REPO}/pulls/1", headers=headers())
    missing = await harness.http.get("/v1/repos/octo/nothing", headers=headers())
    assert hidden.status_code == missing.status_code == 404
    assert hidden.json() == missing.json()

    harness.gateway.hidden.clear()
    visible = await harness.http.get(f"/v1/repos/{REPO}/pulls/1", headers=headers())
    assert visible.status_code == 403
    assert visible.json()["code"] == "forbidden"


async def test_github_being_down_is_502_github_unavailable(harness: Harness) -> None:
    harness.gateway.refuse["me"] = GitHubUnavailableError("GitHub answered 503")
    response = await harness.http.get("/v1/me", headers=headers())
    assert response.status_code == 502
    assert response.json()["code"] == "github-unavailable"


async def test_an_unknown_route_is_a_problem_too(client: AsyncClient) -> None:
    response = await client.get("/v1/nothing", headers=headers())
    assert response.status_code == 404
    assert response.json()["code"] == "not-found"
    wrong = await client.put("/healthy")
    assert wrong.json()["code"] == "http-error"


async def test_the_poller_starts_and_stops_with_the_app(
    keyring: FakeKeyring, gateway: FakeGateway
) -> None:
    app = create_app(
        build_settings(poll_seconds=3600), keyring_transport=keyring.transport(), gateway=gateway
    )
    async with LifespanManager(app):
        poller = app.state.container.poller
        assert poller is not None
        assert not poller.done()
    assert poller.cancelled()
    assert gateway.closed
