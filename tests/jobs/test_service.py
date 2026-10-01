"""The subscription service and its poller, driven directly with a fake GitHub and clock."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

import httpx
import pytest

from github_api.github.errors import (
    GitHubNotFoundError,
    GitHubRateLimitedError,
    GitHubUnauthorizedError,
    GitHubUnavailableError,
)
from github_api.github.fake import FakeGateway
from github_api.github.models import (
    CheckRun,
    Checks,
    GitHubCredential,
    Pull,
    PullDetail,
    Repo,
    RunStatus,
)
from github_api.jobs.models import EXPIRED, FAILED, FIRED, RUNNING, SubscriptionRefusedError
from github_api.jobs.service import (
    Limits,
    NewSubscription,
    SubscriptionNotFoundError,
    Subscriptions,
)
from github_api.jobs.store import MEMORY, SubscriptionStore
from tests.conftest import ACCOUNT, PROFILE, REPO, SECRET, SIGNAL_URL, Signals

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

CRED = GitHubCredential(headers={"Authorization": "Bearer ghu_held"}, kind="app")
START = 1_000_000.0


class Clock:
    def __init__(self) -> None:
        self.now = START

    def __call__(self) -> float:
        return self.now


async def no_sleep(_seconds: float) -> None:
    return None


def limits(**overrides: Any) -> Limits:
    values: dict[str, Any] = {
        "default_seconds": 3600.0,
        "max_seconds": 7 * 24 * 3600.0,
        "per_account": 50,
        "retention_seconds": 3600.0,
        "hold_seconds": 3600.0,
        "refresh_margin_seconds": 60.0,
        "poll_seconds": 30.0,
    }
    return Limits(**{**values, **overrides})


class World:
    def __init__(self, **overrides: Any) -> None:
        self.gateway = FakeGateway()
        self.gateway.seed_repo(Repo(full_name=REPO))
        self.gateway.seed_pull(
            PullDetail(pull=Pull(repo=REPO, number=42, title="t", state="open", head="f"))
        )
        self.clock = Clock()
        self.signals = Signals()
        self.store = SubscriptionStore(MEMORY)
        self.service = Subscriptions(
            self.store,
            self.gateway,
            httpx.AsyncClient(transport=self.signals.transport()),
            limits=limits(**overrides),
            clock=self.clock,
            sleep=no_sleep,
        )

    def spec(self, kind: str = "pull_merged", **extra: Any) -> NewSubscription:
        values: dict[str, Any] = {
            "repo": REPO,
            "kind": kind,
            "target": {"number": 42},
            "signal_url": SIGNAL_URL,
            "secret": SECRET,
        }
        return NewSubscription(**{**values, **extra})

    def merge(self) -> None:
        detail = self.gateway.pull_requests[(REPO, 42)]
        self.gateway.seed_pull(
            detail.model_copy(update={"pull": detail.pull.model_copy(update={"state": "merged"})})
        )


@pytest.fixture
def world() -> Iterator[World]:
    found = World()
    yield found
    found.store.close()


async def test_pull_merged_fires_and_closed_without_merging_fails(world: World) -> None:
    sub = await world.service.create(ACCOUNT, PROFILE, CRED, world.spec())
    assert sub.state == RUNNING
    await world.service.tick()
    world.merge()
    await world.service.tick()
    ended = world.service.owned(ACCOUNT, sub.id)
    assert (ended.state, ended.summary) == (FIRED, f"{REPO}#42 was merged")

    detail = world.gateway.pull_requests[(REPO, 42)]
    world.gateway.seed_pull(
        detail.model_copy(update={"pull": detail.pull.model_copy(update={"state": "closed"})})
    )
    closed = await world.service.create(ACCOUNT, PROFILE, CRED, world.spec())
    assert closed.state == FAILED
    assert closed.summary == f"{REPO}#42 was closed without merging"
    await world.service.sender.drain()
    assert [body["state"] for body in world.signals.bodies()] == [FIRED, FAILED]


async def test_run_completed_fires_with_the_conclusion(world: World) -> None:
    world.gateway.runs[(REPO, "991")] = RunStatus(id="991", name="tests", status="in_progress")
    spec = world.spec("run_completed", target={"run": "991"})
    sub = await world.service.create(ACCOUNT, PROFILE, CRED, spec)
    assert sub.label == f"{REPO} job 991"
    world.gateway.runs[(REPO, "991")] = RunStatus(
        id="991", name="", status="completed", conclusion=""
    )
    await world.service.tick()
    ended = world.service.owned(ACCOUNT, sub.id)
    assert ended.summary == f"{REPO} job 991 finished: none"
    assert ended.facts == {"conclusion": "none", "name": ""}


async def test_checks_settled_on_a_ref_waits_for_any_check_at_all(world: World) -> None:
    spec = world.spec("checks_settled", target={"ref": "main"})
    sub = await world.service.create(ACCOUNT, PROFILE, CRED, spec)
    assert sub.label == f"{REPO}@main"
    assert sub.state == RUNNING
    world.gateway.check_runs[(REPO, "main")] = Checks(
        summary="success",
        checks=[CheckRun(id="1", name="t", status="completed", conclusion="skipped")],
    )
    await world.service.tick()
    assert world.service.owned(ACCOUNT, sub.id).facts["conclusion"] == "success"


async def test_a_held_credential_lapses_at_the_earliest_of_its_limits(world: World) -> None:
    expiring = GitHubCredential(headers=CRED.headers, kind="app", expires_at=START + 600)
    sub = await world.service.create(ACCOUNT, PROFILE, expiring, world.spec())
    assert world.service.holds(sub.id)
    world.clock.now = START + 600 - 60
    assert not world.service.holds(sub.id)
    await world.service.tick()
    assert sub.id not in world.service._held

    nearly = GitHubCredential(headers=CRED.headers, kind="app", expires_at=START + 30)
    world.clock.now = START
    other = await world.service.create(ACCOUNT, PROFILE, nearly, world.spec())
    assert not world.service.holds(other.id)


async def test_a_401_drops_the_held_credential(world: World) -> None:
    sub = await world.service.create(ACCOUNT, PROFILE, CRED, world.spec())
    world.gateway.rejected.add("Bearer ghu_held")
    await world.service.tick()
    assert not world.service.holds(sub.id)
    assert world.service.owned(ACCOUNT, sub.id).state == RUNNING


async def test_a_rate_limit_pauses_the_poller_until_reset(world: World) -> None:
    await world.service.create(ACCOUNT, PROFILE, CRED, world.spec())
    world.gateway.refuse["pull_summary"] = GitHubRateLimitedError("slow", 120)
    await world.service.tick()
    del world.gateway.refuse["pull_summary"]
    calls = len(world.gateway.calls)
    world.clock.now += 60
    await world.service.tick()
    assert len(world.gateway.calls) == calls
    world.clock.now += 61
    await world.service.tick()
    assert len(world.gateway.calls) == calls + 1


async def test_a_target_that_disappears_fails_and_an_outage_waits(world: World) -> None:
    sub = await world.service.create(ACCOUNT, PROFILE, CRED, world.spec())
    world.gateway.refuse["pull_summary"] = GitHubUnavailableError("down")
    await world.service.tick()
    assert world.service.owned(ACCOUNT, sub.id).state == RUNNING
    world.gateway.refuse["pull_summary"] = GitHubNotFoundError("gone")
    await world.service.tick()
    ended = world.service.owned(ACCOUNT, sub.id)
    assert ended.state == FAILED
    assert "can no longer be seen" in ended.summary


async def test_refresh_holds_the_presented_credential_and_raises_a_401(world: World) -> None:
    sub = await world.service.create(ACCOUNT, PROFILE, CRED, world.spec())
    world.service._held.clear()
    fresh = GitHubCredential(headers={"Authorization": "Bearer ghu_fresh"}, kind="app")
    await world.service.refresh(sub, fresh)
    assert world.service.holds(sub.id)
    world.gateway.rejected.add("Bearer ghu_fresh")
    with pytest.raises(GitHubUnauthorizedError):
        await world.service.refresh(sub, fresh)


async def test_expiry_signals_and_old_endings_are_purged(world: World) -> None:
    sub = await world.service.create(ACCOUNT, PROFILE, CRED, world.spec(expires_in_seconds=60))
    world.clock.now += 60
    await world.service.tick()
    assert world.service.owned(ACCOUNT, sub.id).state == EXPIRED
    await world.service.sender.drain()
    assert world.signals.bodies()[0]["summary"] == f"Stopped watching {REPO}#42: the watch expired"
    world.clock.now += 3600 + 1
    await world.service.tick()
    with pytest.raises(SubscriptionNotFoundError):
        world.service.owned(ACCOUNT, sub.id)


async def test_a_delivery_outcome_for_a_deleted_subscription_is_ignored(world: World) -> None:
    world.merge()
    sub = await world.service.create(ACCOUNT, PROFILE, CRED, world.spec())
    world.service.delete(ACCOUNT, sub.id)
    await world.service.sender.drain()
    assert world.store.get(sub.id) is None


async def test_a_refused_signal_is_recorded_as_refused(world: World) -> None:
    world.signals.status = 404
    world.merge()
    sub = await world.service.create(ACCOUNT, PROFILE, CRED, world.spec())
    await world.service.sender.drain()
    stored = world.store.get(sub.id)
    assert stored is not None
    assert stored.delivery == "refused"


async def test_aclose_cancels_deliveries_still_retrying(world: World) -> None:
    gate = asyncio.Event()

    async def stuck(_seconds: float) -> None:
        await gate.wait()

    world.signals.status = 503
    world.service.sender._sleep = stuck
    world.merge()
    await world.service.create(ACCOUNT, PROFILE, CRED, world.spec())
    await asyncio.sleep(0)
    assert world.service.sender._tasks
    await world.service.aclose()
    assert not world.service.sender._tasks


async def test_run_forever_ticks_and_survives_a_failed_tick(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    ticks = 0

    async def tick() -> None:
        nonlocal ticks
        ticks += 1
        if ticks == 1:
            message = "boom"
            raise RuntimeError(message)
        if ticks == 2:
            raise asyncio.CancelledError

    world.service.tick = tick  # type: ignore[method-assign]
    with caplog.at_level(logging.ERROR), pytest.raises(asyncio.CancelledError):
        await world.service.run_forever()
    assert ticks == 2
    assert "subscription_poll_failed" in caplog.text


@pytest.mark.parametrize(
    ("spec", "fragment"),
    [
        ({"kind": "nope"}, "unknown kind `nope`"),
        ({"target": {"numbr": 1}}, "unknown target field `numbr`"),
        ({"target": {"number": True}}, "`target.number`"),
        ({"target": {"number": 0}}, "`target.number`"),
        ({"target": {"ref": "main"}}, "`pull_merged` needs `number`"),
        ({"kind": "run_completed", "target": {"run": ""}}, "`run_completed` needs `run`"),
        ({"expires_in_seconds": -1.0}, "already have expired"),
        ({"signal_url": "https://"}, "absolute http or https URL"),
        ({"secret": "x" * 513}, "16 to 512 characters"),
    ],
)
async def test_refusals_name_the_fix(world: World, spec: dict[str, Any], fragment: str) -> None:
    with pytest.raises(SubscriptionRefusedError, match=fragment.replace("`", ".")):
        await world.service.create(ACCOUNT, PROFILE, CRED, world.spec(**spec))


async def test_the_signal_allowlist_is_enforced() -> None:
    world = World(url_prefixes=("https://lucy.internal/",))
    with pytest.raises(
        SubscriptionRefusedError, match=r"must start with `https://lucy\.internal/`"
    ):
        await world.service.create(ACCOUNT, PROFILE, CRED, world.spec())
    allowed = world.spec(signal_url="https://lucy.internal/v1/signals/x")
    assert (await world.service.create(ACCOUNT, PROFILE, CRED, allowed)).state == RUNNING
    world.store.close()


def test_a_database_file_gets_its_directory(tmp_path: Path) -> None:
    store = SubscriptionStore(str(tmp_path / "var" / "subs.sqlite3"))
    assert store.healthy()
    store.close()
    assert not store.healthy()
