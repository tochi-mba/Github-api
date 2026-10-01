"""The sibling side of the jobs contract, through the app: subscribe, sweep, release, signal."""

from __future__ import annotations

from typing import Any

from lucy_signals import HEADER, verify_signature

from github_api.github.models import CheckRun, Checks, Pull, PullDetail, Review
from tests.conftest import (
    APP_TOKEN,
    OTHER_ACCOUNT,
    REPO,
    SECRET,
    SIGNAL_URL,
    Harness,
    build_settings,
    connect,
    headers,
)

PENDING = CheckRun(id="1", name="tests", status="in_progress")
PASSED = CheckRun(id="1", name="tests", status="completed", conclusion="success")
FAILED = CheckRun(
    id="2", name="lint", status="completed", conclusion="failure", failing_steps=["ruff"]
)


def body(kind: str = "checks_settled", **target: Any) -> dict[str, Any]:
    """What the hub's `repos.watch` sends."""
    return {
        "repo": REPO,
        "kind": kind,
        "target": target or {"number": 42},
        "expires_in_seconds": 3600,
        "signal": {"url": SIGNAL_URL, "secret": SECRET},
    }


def seed(harness: Harness, *checks: CheckRun) -> None:
    harness.gateway.seed_pull(
        PullDetail(pull=Pull(repo=REPO, number=42, title="t", state="open", head="f"))
    )
    harness.gateway.check_runs[(REPO, "pull/42")] = Checks(summary="pending", checks=list(checks))


async def subscribe(harness: Harness, document: dict[str, Any]) -> str:
    response = await harness.http.post("/v1/subscriptions", json=document, headers=headers())
    assert response.status_code == 201, response.text
    assert set(response.json()) == {"id", "state", "expires_at"}
    sub_id: str = response.json()["id"]
    return sub_id


async def test_subscribe_answers_201_running_and_never_echoes_the_secret(
    harness: Harness,
) -> None:
    seed(harness, PENDING)
    sub_id = await subscribe(harness, body())
    read = await harness.http.get(f"/v1/subscriptions/{sub_id}", headers=headers())
    assert read.status_code == 200
    view = read.json()
    assert view["state"] == "running"
    assert view["kind"] == "checks_settled"
    assert view["target"] == {"number": 42}
    assert SECRET not in read.text
    assert SIGNAL_URL not in read.text
    assert harness.signals.received == []


async def test_checks_settled_fires_only_when_every_check_concluded(harness: Harness) -> None:
    seed(harness, PASSED, PENDING)
    sub_id = await subscribe(harness, body())
    await harness.container.subscriptions.tick()
    assert harness.signals.received == []

    harness.gateway.check_runs[(REPO, "pull/42")] = Checks(
        summary="failure", checks=[PASSED, FAILED]
    )
    await harness.container.subscriptions.tick()
    await harness.container.subscriptions.sender.drain()

    [request] = harness.signals.received
    assert str(request.url) == SIGNAL_URL
    assert verify_signature(SECRET, request.headers[HEADER], request.content)
    [signal] = harness.signals.bodies()
    assert signal["state"] == "fired"
    assert signal["summary"] == f"CI on {REPO}#42 failed: 1 of 2 checks"
    assert signal["facts"] == {"conclusion": "failure", "checks": 2, "failed": 1}
    assert signal["excerpt"] == "lint: failure (ruff)"

    view = (await harness.http.get(f"/v1/subscriptions/{sub_id}", headers=headers())).json()
    assert view["state"] == "fired"
    stored = harness.container.store.get(sub_id)
    assert stored is not None
    assert stored.delivery == "delivered"


async def test_a_condition_that_already_holds_fires_at_once(harness: Harness) -> None:
    seed(harness, PASSED)
    sub_id = await subscribe(harness, body())
    await harness.container.subscriptions.sender.drain()
    assert harness.signals.bodies()[0]["summary"] == f"CI on {REPO}#42 passed"
    view = (await harness.http.get(f"/v1/subscriptions/{sub_id}", headers=headers())).json()
    assert view["facts"]["conclusion"] == "success"


async def test_the_hubs_sweep_drives_a_subscription_the_poller_cannot(harness: Harness) -> None:
    seed(harness, PENDING)
    sub_id = await subscribe(harness, {**body(), "expires_in_seconds": 7200})
    service = harness.container.subscriptions
    harness.clock.advance(harness.container.settings.credential_hold_seconds + 1)
    await service.tick()
    assert not service.holds(sub_id)

    harness.gateway.check_runs[(REPO, "pull/42")] = Checks(summary="success", checks=[PASSED])
    await service.tick()
    assert harness.signals.received == []

    swept = await harness.http.get(f"/v1/subscriptions/{sub_id}", headers=headers())
    assert swept.json()["state"] == "fired"
    await service.sender.drain()
    assert len(harness.signals.received) == 1


async def test_a_sweep_without_a_credential_answers_the_stored_state(harness: Harness) -> None:
    seed(harness, PENDING)
    sub_id = await subscribe(harness, body())
    harness.clock.advance(harness.container.settings.credential_cache_seconds + 1)
    harness.keyring.sealed = True
    swept = await harness.http.get(f"/v1/subscriptions/{sub_id}", headers=headers())
    assert swept.status_code == 200
    assert swept.json()["state"] == "running"


async def test_a_sweep_refreshes_a_refused_credential_once(harness: Harness) -> None:
    seed(harness, PENDING)
    sub_id = await subscribe(harness, body())
    harness.gateway.rejected.add(APP_TOKEN)
    connect(harness.keyring, "Bearer ghu_second")
    harness.gateway.check_runs[(REPO, "pull/42")] = Checks(summary="success", checks=[PASSED])
    swept = await harness.http.get(f"/v1/subscriptions/{sub_id}", headers=headers())
    assert swept.json()["state"] == "fired"


async def test_an_ended_subscription_is_not_looked_at_again(harness: Harness) -> None:
    seed(harness, PASSED)
    sub_id = await subscribe(harness, body())
    calls = len(harness.gateway.calls)
    await harness.http.get(f"/v1/subscriptions/{sub_id}", headers=headers())
    assert len(harness.gateway.calls) == calls


async def test_another_accounts_subscription_is_404(harness: Harness) -> None:
    seed(harness, PENDING)
    sub_id = await subscribe(harness, body())
    connect(harness.keyring, account_id=OTHER_ACCOUNT)
    theirs = await harness.http.get(f"/v1/subscriptions/{sub_id}", headers=headers(OTHER_ACCOUNT))
    nobodys = await harness.http.get("/v1/subscriptions/sub_nothing", headers=headers())
    assert theirs.status_code == nobodys.status_code == 404
    assert theirs.json() == nobodys.json()
    refused = await harness.http.delete(
        f"/v1/subscriptions/{sub_id}", headers=headers(OTHER_ACCOUNT)
    )
    assert refused.status_code == 404


async def test_delete_stops_looking_and_a_second_delete_is_404(harness: Harness) -> None:
    seed(harness, PENDING)
    sub_id = await subscribe(harness, body())
    assert (
        await harness.http.delete(f"/v1/subscriptions/{sub_id}", headers=headers())
    ).status_code == 204
    assert not harness.container.subscriptions.holds(sub_id)
    again = await harness.http.delete(f"/v1/subscriptions/{sub_id}", headers=headers())
    assert again.status_code == 404


async def test_an_unknown_kind_is_422_naming_the_known_ones(harness: Harness) -> None:
    response = await harness.http.post(
        "/v1/subscriptions", json=body("pr_merged"), headers=headers()
    )
    assert response.status_code == 422
    assert response.json()["code"] == "unknown-kind"
    for kind in ("checks_settled", "pull_merged", "review_submitted", "run_completed"):
        assert kind in response.json()["detail"]


async def test_a_target_that_does_not_fit_the_kind_is_422(harness: Harness) -> None:
    both = await harness.http.post(
        "/v1/subscriptions", json=body(number=42, ref="main"), headers=headers()
    )
    assert both.status_code == 422
    assert "exactly one of" in both.json()["detail"]


async def test_a_target_that_cannot_be_seen_is_404_and_nothing_is_kept(harness: Harness) -> None:
    response = await harness.http.post(
        "/v1/subscriptions", json=body("pull_merged", number=9), headers=headers()
    )
    assert response.status_code == 404
    assert harness.container.store.running() == []


async def test_the_signal_url_and_secret_are_checked(harness: Harness) -> None:
    seed(harness, PENDING)
    document = body()
    document["signal"] = {"url": "ftp://lucy.test/x", "secret": SECRET}
    response = await harness.http.post("/v1/subscriptions", json=document, headers=headers())
    assert response.status_code == 422
    assert "signal.url" in response.json()["detail"]

    document["signal"] = {"url": SIGNAL_URL, "secret": "short"}
    response = await harness.http.post("/v1/subscriptions", json=document, headers=headers())
    assert response.status_code == 422
    assert "signal.secret" in response.json()["detail"]


async def test_an_expiry_already_past_is_refused(harness: Harness) -> None:
    document = body()
    del document["expires_in_seconds"]
    document["expires_at"] = "2020-01-01T00:00:00"
    response = await harness.http.post("/v1/subscriptions", json=document, headers=headers())
    assert response.status_code == 422
    assert "already have expired" in response.json()["detail"]


async def test_lifetimes_are_capped_and_an_expiry_signals(harness: Harness) -> None:
    seed(harness, PENDING)
    document = body()
    document["expires_in_seconds"] = 10 * 365 * 24 * 3600
    document["expires_at"] = "2030-01-01T00:00:00+00:00"
    sub_id = await subscribe(harness, document)
    stored = harness.container.store.get(sub_id)
    assert stored is not None
    assert (
        stored.expires_at - stored.created_at == harness.container.settings.subscription_max_seconds
    )

    harness.clock.advance(harness.container.settings.subscription_max_seconds)
    view = (await harness.http.get(f"/v1/subscriptions/{sub_id}", headers=headers())).json()
    assert view["state"] == "expired"
    await harness.container.subscriptions.sender.drain()
    assert harness.signals.bodies()[0]["state"] == "expired"


async def test_an_account_has_a_cap_on_open_subscriptions(harness: Harness) -> None:
    seed(harness, PENDING)
    harness.container.subscriptions._limits = harness.container.subscriptions._limits.__class__(
        **{**_limits(harness), "per_account": 1}
    )
    await subscribe(harness, body())
    response = await harness.http.post("/v1/subscriptions", json=body(), headers=headers())
    assert response.status_code == 409
    assert response.json()["code"] == "too-many-subscriptions"


async def test_review_submitted_waits_past_the_reviews_already_there(harness: Harness) -> None:
    seed(harness, PENDING)
    harness.gateway.reviews[(REPO, 42)] = [Review(author="old", state="commented", body="hm")]
    sub_id = await subscribe(harness, body("review_submitted", number=42))
    stored = harness.container.store.get(sub_id)
    assert stored is not None
    assert stored.baseline == 1
    await harness.container.subscriptions.tick()
    assert harness.signals.received == []

    harness.gateway.reviews[(REPO, 42)].append(Review(author="rev", state="approved", body="ok"))
    await harness.container.subscriptions.tick()
    await harness.container.subscriptions.sender.drain()
    [signal] = harness.signals.bodies()
    assert signal["summary"] == f"rev approved {REPO}#42"
    assert signal["excerpt"] == "ok"


def _limits(harness: Harness) -> dict[str, Any]:
    limits = harness.container.subscriptions._limits
    return {name: getattr(limits, name) for name in limits.__slots__}


def test_settings_carry_the_signal_allowlist() -> None:
    settings = build_settings(signal_url_prefixes=["https://lucy.test/"])
    assert settings.signal_url_prefixes == ["https://lucy.test/"]
