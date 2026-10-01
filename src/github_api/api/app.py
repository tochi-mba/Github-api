"""Application factory."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from fastapi import FastAPI

from github_api import __version__
from github_api.api.errors import register_exception_handlers
from github_api.api.routers import ROUTERS
from github_api.container import build_container
from github_api.core.config import Settings, load_settings

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Awaitable, Callable

    from httpx import AsyncBaseTransport
    from keyring_client import Clock

    from github_api.github.protocols import GitHubGateway


def create_app(  # noqa: PLR0913 - settings, and one seam per outbound dependency and time
    settings: Settings | None = None,
    *,
    transport: AsyncBaseTransport | None = None,
    keyring_transport: AsyncBaseTransport | None = None,
    signal_transport: AsyncBaseTransport | None = None,
    gateway: GitHubGateway | None = None,
    clock: Clock | None = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> FastAPI:
    """Build the application.

    Args:
        settings: configuration; loaded from the environment when omitted.
        transport: httpx transport for GitHub (tests: respx or a mock).
        keyring_transport: httpx transport for keyring's keys and credentials
            (tests: ``keyring_client.testing.FakeKeyring().transport()``).
        signal_transport: httpx transport for the hub's signal endpoint.
        gateway: a whole GitHub gateway (tests: ``FakeGateway``); replaces ``transport``.
        clock: keyring's clock protocol; the system clock when omitted.
        sleep: how the poller and signal retries wait.
    """
    resolved = settings if settings is not None else load_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
        container = build_container(
            resolved,
            transport=transport,
            keyring_transport=keyring_transport,
            signal_transport=signal_transport,
            gateway=gateway,
            clock=clock,
            sleep=sleep,
        )
        app.state.container = container
        container.start()
        try:
            yield
        finally:
            await container.aclose()

    app = FastAPI(
        title="Github API",
        description="The LUCY family's repos service: GitHub, reduced to what a person says.",
        version=__version__,
        lifespan=lifespan,
    )
    register_exception_handlers(app)
    for router in ROUTERS:
        app.include_router(router)
    return app
