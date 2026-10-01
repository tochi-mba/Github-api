"""Subscriptions: opened by a request, watched by a poller, ended by one signed signal.

The contract is the hub's (``docs/jobs.md`` in LUCY-assistant). What this module decides is
*how* the watching happens, given that the poller has no person's token once the request that
opened a subscription has returned:

* **At subscribe time** the request carries the person's token, so the condition is evaluated
  at once (which also checks the target exists and counts the reviews a ``review_submitted``
  waits past), and the GitHub credential resolved for that request is **held in memory** for
  the background poller -- never written down -- until the earliest of: keyring's expiry for
  it (less the refresh margin), the subscription's own expiry, and ``credential_hold_seconds``.
* **In the background**, every ``poll_seconds``, the poller expires what is past its lifetime
  and evaluates every running subscription it still holds a credential for. A 401 drops the
  held credential; a rate limit pauses polling until GitHub's reset.
* **When the hub sweeps** (``GET /v1/subscriptions/{id}`` under its standing grant), the route
  presents a fresh credential: the subscription is evaluated then, and the credential is held
  again. So once the held credential has lapsed -- or after a restart, which forgets them all
  -- the subscription stays ``running`` and the hub's sweep drives it to its end.

Every ending (fired, failed, expired) is saved first and then signalled in the background.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from github_api.github.errors import (
    GitHubError,
    GitHubNotFoundError,
    GitHubRateLimitedError,
    GitHubUnauthorizedError,
)
from github_api.jobs.conditions import evaluate
from github_api.jobs.delivery import SignalSender
from github_api.jobs.models import (
    EXPIRED,
    FAILED,
    RUNNING,
    Subscription,
    SubscriptionRefusedError,
    Verdict,
    kind_of,
    target_for,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    import httpx

    from github_api.github.models import GitHubCredential
    from github_api.github.protocols import GitHubGateway
    from github_api.jobs.store import SubscriptionStore

log = logging.getLogger(__name__)

MIN_SECRET_CHARS = 16
MAX_SECRET_CHARS = 512


class SubscriptionNotFoundError(Exception):
    """No such subscription for this account. Another account's is the same answer."""


@dataclass(frozen=True, slots=True)
class Limits:
    """How long, how many, and how the poller holds credentials."""

    default_seconds: float
    max_seconds: float
    per_account: int
    retention_seconds: float
    hold_seconds: float
    refresh_margin_seconds: float
    poll_seconds: float
    url_prefixes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True, repr=False)
class NewSubscription:
    """A subscribe request, as the route received it."""

    repo: str
    kind: str
    target: dict[str, Any]
    signal_url: str
    secret: str
    expires_in_seconds: float | None = None
    expires_at: float | None = None


class Subscriptions:
    """The subscription lifecycle, and the background poller."""

    def __init__(  # noqa: PLR0913 - the table, GitHub, the signals' client, rules, time
        self,
        store: SubscriptionStore,
        gateway: GitHubGateway,
        signal_client: httpx.AsyncClient,
        *,
        limits: Limits,
        clock: Callable[[], float],
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._store = store
        self._gateway = gateway
        self._limits = limits
        self._clock = clock
        self._sleep = sleep
        self.sender = SignalSender(signal_client, on_delivered=self._delivered, sleep=sleep)
        self._held: dict[str, tuple[GitHubCredential, float]] = {}
        self._paused_until = 0.0

    # ------------------------------------------------------------------ requests

    async def create(
        self, account_id: str, profile: str, cred: GitHubCredential, spec: NewSubscription
    ) -> Subscription:
        """Open one, after one look. Raises SubscriptionRefusedError or what GitHub raised."""
        kind = kind_of(spec.kind)
        target = target_for(kind, spec.target)
        self._check_signal(spec.signal_url, spec.secret)
        now = self._clock()
        expires_at = self._lifetime(now, spec)
        if self._store.count_running(account_id) >= self._limits.per_account:
            message = (
                f"this account already has {self._limits.per_account} open subscriptions; "
                "cancel one first"
            )
            raise SubscriptionRefusedError(message, code="too-many-subscriptions")
        sub = Subscription(
            id=f"sub_{secrets.token_urlsafe(18)}",
            account_id=account_id,
            profile=profile,
            repo=spec.repo,
            kind=kind,
            target=target,
            signal_url=spec.signal_url,
            secret=spec.secret,
            state=RUNNING,
            created_at=now,
            expires_at=expires_at,
        )
        verdict = await evaluate(self._gateway, cred, sub)
        self._store.add(sub)
        self._hold(sub, cred, now)
        return self._apply(sub, verdict)

    def owned(self, account_id: str, sub_id: str) -> Subscription:
        """The caller's subscription, expired first if its time is up."""
        sub = self._store.get(sub_id)
        if sub is None or sub.account_id != account_id:
            raise SubscriptionNotFoundError(sub_id)
        return self._expire_if_due(sub)

    async def refresh(self, sub: Subscription, cred: GitHubCredential) -> Subscription:
        """Look now under a credential a request presented, and hold it for the poller.

        Raises GitHubUnauthorizedError so the caller can refresh the credential once; every
        other GitHub failure leaves the subscription as it was.
        """
        if sub.state != RUNNING:
            return sub
        self._hold(sub, cred, self._clock())
        return await self._look(sub, cred)

    def delete(self, account_id: str, sub_id: str) -> None:
        self.owned(account_id, sub_id)
        self._store.delete(sub_id)
        self._held.pop(sub_id, None)

    def holds(self, sub_id: str) -> bool:
        """Whether the poller currently holds a credential for this subscription."""
        held = self._held.get(sub_id)
        return held is not None and held[1] > self._clock()

    # ------------------------------------------------------------------ the poller

    async def tick(self) -> None:
        """One pass: forget old endings, expire what is due, look at what can be seen."""
        now = self._clock()
        self._store.purge(now - self._limits.retention_seconds)
        for found in self._store.running():
            sub = self._expire_if_due(found)
            held = self._held.get(sub.id)
            if sub.state != RUNNING or held is None:
                continue
            if held[1] <= now:
                del self._held[sub.id]
                continue
            if now < self._paused_until:
                continue
            try:
                await self._look(sub, held[0])
            except GitHubUnauthorizedError:
                continue

    async def run_forever(self) -> None:
        """Tick every ``poll_seconds`` until cancelled. A failed tick is logged, not fatal."""
        while True:
            await self._sleep(self._limits.poll_seconds)
            try:
                await self.tick()
            except Exception:
                log.exception("subscription_poll_failed")

    async def aclose(self) -> None:
        self._held.clear()
        await self.sender.aclose()

    # ------------------------------------------------------------------ internals

    async def _look(self, sub: Subscription, cred: GitHubCredential) -> Subscription:
        try:
            verdict = await evaluate(self._gateway, cred, sub)
        except GitHubUnauthorizedError:
            self._held.pop(sub.id, None)
            raise
        except GitHubNotFoundError:
            verdict = Verdict(
                state=FAILED, summary=f"{sub.label} can no longer be seen with this connection"
            )
        except GitHubRateLimitedError as exc:
            self._paused_until = self._clock() + exc.retry_after
            return sub
        except GitHubError:
            return sub
        return self._apply(sub, verdict)

    def _apply(self, sub: Subscription, verdict: Verdict) -> Subscription:
        baseline = sub.baseline if verdict.baseline is None else verdict.baseline
        if verdict.state == RUNNING:
            if baseline == sub.baseline:
                return sub
            counted = replace(sub, baseline=baseline)
            self._store.save(counted)
            return counted
        ended = replace(
            sub,
            state=verdict.state,
            summary=verdict.summary,
            facts=dict(verdict.facts),
            excerpt=verdict.excerpt,
            baseline=baseline,
        )
        self._store.save(ended)
        self._held.pop(sub.id, None)
        self.sender.send(ended)
        return ended

    def _expire_if_due(self, sub: Subscription) -> Subscription:
        if sub.state != RUNNING or self._clock() < sub.expires_at:
            return sub
        return self._apply(
            sub, Verdict(state=EXPIRED, summary=f"Stopped watching {sub.label}: the watch expired")
        )

    def _hold(self, sub: Subscription, cred: GitHubCredential, now: float) -> None:
        until = min(now + self._limits.hold_seconds, sub.expires_at)
        if cred.expires_at is not None:
            until = min(until, cred.expires_at - self._limits.refresh_margin_seconds)
        if until > now:
            self._held[sub.id] = (cred, until)

    def _lifetime(self, now: float, spec: NewSubscription) -> float:
        asked = [
            moment
            for moment in (
                None if spec.expires_in_seconds is None else now + spec.expires_in_seconds,
                spec.expires_at,
            )
            if moment is not None
        ]
        requested = min(asked) if asked else now + self._limits.default_seconds
        if requested <= now:
            message = "the subscription would already have expired; ask for a future expiry"
            raise SubscriptionRefusedError(message)
        return min(requested, now + self._limits.max_seconds)

    def _check_signal(self, url: str, secret: str) -> None:
        parts = urlsplit(url)
        if parts.scheme not in {"http", "https"} or not parts.netloc:
            message = "`signal.url` must be an absolute http or https URL"
            raise SubscriptionRefusedError(message)
        prefixes = self._limits.url_prefixes
        if prefixes and not url.startswith(prefixes):
            message = "`signal.url` is not one this service may call; it must start with " + (
                " or ".join(f"`{prefix}`" for prefix in prefixes)
            )
            raise SubscriptionRefusedError(message)
        if not MIN_SECRET_CHARS <= len(secret) <= MAX_SECRET_CHARS:
            message = f"`signal.secret` must be {MIN_SECRET_CHARS} to {MAX_SECRET_CHARS} characters"
            raise SubscriptionRefusedError(message)

    def _delivered(self, sub_id: str, outcome: str) -> None:
        sub = self._store.get(sub_id)
        if sub is not None:
            self._store.save(replace(sub, delivery=outcome))


__all__ = [
    "Limits",
    "NewSubscription",
    "SubscriptionNotFoundError",
    "Subscriptions",
]
