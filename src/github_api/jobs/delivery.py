"""Sending the one signal that ends a subscription, in the background.

``lucy_signals.deliver`` signs the body with the subscription's secret and retries a refused
connection or a 5xx over about two minutes, so it never runs inside a request: each send is a
task, tracked so shutdown can cancel what is still retrying. A signal that never lands costs
latency, not the ending -- the hub's sweep asks ``GET /v1/subscriptions/{id}`` and reads the
state from there.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from lucy_signals import Signal, deliver

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    import httpx
    from lucy_signals import Outcome

    from github_api.jobs.models import Subscription

log = logging.getLogger(__name__)

SCALARS = (str, int, float, bool)


def signal_of(sub: Subscription) -> Signal:
    """The signal for an ended subscription: what happened, never the result."""
    facts = {key: value for key, value in sub.facts.items() if isinstance(value, SCALARS)}
    return Signal(
        state=sub.state,  # type: ignore[arg-type]  # only ended subscriptions are sent
        summary=sub.summary,
        facts=dict(list(facts.items())[:12]),
        excerpt=sub.excerpt,
    )


class SignalSender:
    """Delivers signals as tracked background tasks."""

    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        on_delivered: Callable[[str, str], None],
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._client = client
        self._on_delivered = on_delivered
        self._sleep = sleep
        self._tasks: set[asyncio.Task[None]] = set()

    def send(self, sub: Subscription) -> None:
        """Start delivering; return at once."""
        task = asyncio.create_task(self._deliver(sub))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def drain(self) -> None:
        """Wait for every delivery in flight. For tests and orderly shutdown."""
        while self._tasks:
            await asyncio.gather(*tuple(self._tasks))

    async def aclose(self) -> None:
        for task in tuple(self._tasks):
            task.cancel()
        await asyncio.gather(*tuple(self._tasks), return_exceptions=True)
        await self._client.aclose()

    async def _deliver(self, sub: Subscription) -> None:
        outcome: Outcome = await deliver(
            self._client,
            url=sub.signal_url,
            secret=sub.secret,
            signal=signal_of(sub),
            sleep=self._sleep,
        )
        log.info("signal_%s", outcome, extra={"subscription": sub.id, "state": sub.state})
        self._on_delivered(sub.id, outcome)


__all__ = ["SignalSender", "signal_of"]
