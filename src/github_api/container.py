"""Process-scoped object graph: built once per app, closed in the lifespan.

Every outbound seam can be replaced here, which is what keeps the suite offline:
``transport`` for GitHub, ``keyring_transport`` for keyring's keys and internal surface,
``signal_transport`` for the hub's signal endpoint, ``gateway`` for a whole fake GitHub, and
``clock``/``sleep`` for time.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import httpx
from keyring_client import CredentialClient, JwksClient, SystemClock

from github_api.access import GitHubAccess
from github_api.auth.verifier import TokenVerifier
from github_api.credentials.keyring import KeyringCredentials
from github_api.github.client import GitHubClient
from github_api.github.http import GitHubHttp
from github_api.jobs.service import Limits, Subscriptions
from github_api.jobs.store import SubscriptionStore

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from keyring_client import Clock

    from github_api.core.config import Settings
    from github_api.github.protocols import GitHubGateway


@dataclass(slots=True)
class Container:
    """What every request shares for the life of the process."""

    settings: Settings
    jwks: JwksClient
    verifier: TokenVerifier
    credentials: KeyringCredentials
    gateway: GitHubGateway
    access: GitHubAccess
    store: SubscriptionStore
    subscriptions: Subscriptions
    started_at: float = field(default_factory=time.monotonic)
    poller: asyncio.Task[None] | None = None

    @property
    def uptime_seconds(self) -> float:
        return time.monotonic() - self.started_at

    def start(self) -> None:
        """Start the background poller, unless ``poll_seconds`` is zero."""
        if self.settings.poll_seconds > 0:
            self.poller = asyncio.create_task(self.subscriptions.run_forever())

    async def aclose(self) -> None:
        if self.poller is not None:
            self.poller.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.poller
        await self.subscriptions.aclose()
        await self.gateway.aclose()
        await self.credentials.aclose()
        await self.jwks.aclose()
        self.store.close()


def build_container(  # noqa: PLR0913 - one seam per outbound dependency, and time
    settings: Settings,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    keyring_transport: httpx.AsyncBaseTransport | None = None,
    signal_transport: httpx.AsyncBaseTransport | None = None,
    gateway: GitHubGateway | None = None,
    clock: Clock | None = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> Container:
    """Assemble the graph. No network I/O yet."""
    clock = clock if clock is not None else SystemClock()

    def epoch() -> float:
        return clock.now().timestamp()

    jwks = JwksClient(
        url=settings.keyring_jwks_url,
        clock=clock,
        cache_seconds=settings.jwks_cache_seconds,
        min_refetch_seconds=settings.jwks_min_refetch_seconds,
        timeout_seconds=settings.keyring_timeout_seconds,
        transport=keyring_transport,
    )
    verifier = TokenVerifier(
        jwks=jwks, issuer=settings.keyring_issuer, audience=settings.audience, clock=clock
    )
    credentials = KeyringCredentials(
        CredentialClient(
            base_url=settings.keyring_base_url,
            service_token=settings.keyring_service_token.get_secret_value(),
            timeout_seconds=settings.keyring_timeout_seconds,
            transport=keyring_transport,
        ),
        service=settings.keyring_credential_service,
        refresh_margin_seconds=settings.credential_refresh_margin_seconds,
        default_seconds=settings.credential_cache_seconds,
        max_entries=settings.credential_cache_entries,
        clock=epoch,
    )
    github: GitHubGateway = gateway or GitHubClient(
        GitHubHttp(
            base_url=settings.github_base_url,
            graphql_url=settings.github_graphql_url,
            timeout_seconds=settings.request_timeout_seconds,
            transport=transport,
            clock=epoch,
        ),
        file_chars_max=settings.file_chars_max,
        tree_entries_max=settings.tree_entries_max,
        patch_chars_max=settings.patch_chars_max,
    )
    store = SubscriptionStore(settings.database_path)
    subscriptions = Subscriptions(
        store,
        github,
        httpx.AsyncClient(
            timeout=settings.signal_timeout_seconds,
            transport=signal_transport,
            follow_redirects=False,
        ),
        limits=Limits(
            default_seconds=settings.subscription_default_seconds,
            max_seconds=settings.subscription_max_seconds,
            per_account=settings.subscriptions_per_account,
            retention_seconds=settings.subscription_retention_seconds,
            hold_seconds=settings.credential_hold_seconds,
            refresh_margin_seconds=settings.credential_refresh_margin_seconds,
            poll_seconds=settings.poll_seconds,
            url_prefixes=tuple(settings.signal_url_prefixes),
        ),
        clock=epoch,
        sleep=sleep,
    )
    return Container(
        settings=settings,
        jwks=jwks,
        verifier=verifier,
        credentials=credentials,
        gateway=github,
        access=GitHubAccess(credentials, github),
        store=store,
        subscriptions=subscriptions,
    )


__all__ = ["Container", "build_container"]
