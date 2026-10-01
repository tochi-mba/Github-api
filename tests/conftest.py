"""Shared fixtures: settings, a fake keyring, a fake GitHub, and the app between them."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

import httpx
import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient
from keyring_client.testing import BASE_URL, ISSUER, JWKS_URL, FakeClock, FakeKeyring, mint

from github_api.api.app import create_app
from github_api.core.config import LogFormat, Settings
from github_api.github.fake import FakeGateway
from github_api.github.models import Repo

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from fastapi import FastAPI

    from github_api.container import Container

AUDIENCE = "github-api"
SERVICE_TOKEN = "github-api-service-token-0123456789abcdef"
ACCOUNT = "acct_example"
OTHER_ACCOUNT = "acct_other"
PROFILE = "work"
APP_TOKEN = "Bearer ghu_first"
REPO = "octo/hello"
SIGNAL_URL = "https://lucy.test/v1/signals/wk_1"
SECRET = "a-signal-secret-of-some-length"


def build_settings(**overrides: Any) -> Settings:
    defaults: dict[str, Any] = {
        "_env_file": None,
        "log_format": LogFormat.CONSOLE,
        "keyring_issuer": ISSUER,
        "keyring_jwks_url": JWKS_URL,
        "keyring_base_url": BASE_URL,
        "keyring_service_token": SERVICE_TOKEN,
        "audience": AUDIENCE,
        "database_path": ":memory:",
        "poll_seconds": 0,
    }
    return Settings(**{**defaults, **overrides})


def headers(
    account_id: str = ACCOUNT, *, audience: str = AUDIENCE, profile: str | None = PROFILE
) -> dict[str, str]:
    token = mint(account_id=account_id, audience=audience, issuer=ISSUER)
    found = {"Authorization": f"Bearer {token}"}
    if profile is not None:
        found["X-Keyring-Profile"] = profile
    return found


def connect(
    keyring: FakeKeyring,
    authorization: str = APP_TOKEN,
    *,
    account_id: str = ACCOUNT,
    profile: str = PROFILE,
    expires_at: datetime | None = None,
) -> None:
    keyring.connect(
        account_id=account_id,
        profile=profile,
        service="github",
        headers={"Authorization": authorization},
        expires_at=expires_at,
    )


@dataclass
class Signals:
    """The hub's signal endpoint: records every POST and answers ``status``."""

    status: int = 204
    received: list[httpx.Request] = field(default_factory=list)

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.received.append(request)
        return httpx.Response(self.status)

    def bodies(self) -> list[dict[str, Any]]:
        return [json.loads(request.content) for request in self.received]


@dataclass
class Harness:
    app: FastAPI
    http: AsyncClient
    keyring: FakeKeyring
    gateway: FakeGateway
    clock: FakeClock
    signals: Signals

    @property
    def container(self) -> Container:
        found: Container = self.app.state.container
        return found


async def no_sleep(_seconds: float) -> None:
    return None


@pytest.fixture
def settings() -> Settings:
    return build_settings()


@pytest.fixture
def keyring() -> FakeKeyring:
    found = FakeKeyring(service_tokens={AUDIENCE: SERVICE_TOKEN})
    connect(found)
    return found


@pytest.fixture
def gateway() -> FakeGateway:
    found = FakeGateway()
    found.seed_repo(Repo(full_name=REPO, default_branch="main", url="https://github.test/octo"))
    return found


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def signals() -> Signals:
    return Signals()


@pytest.fixture
async def harness(
    settings: Settings,
    keyring: FakeKeyring,
    gateway: FakeGateway,
    clock: FakeClock,
    signals: Signals,
) -> AsyncIterator[Harness]:
    app = create_app(
        settings,
        keyring_transport=keyring.transport(),
        gateway=gateway,
        signal_transport=signals.transport(),
        clock=clock,
        sleep=no_sleep,
    )
    async with (
        LifespanManager(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http,
    ):
        yield Harness(
            app=app, http=http, keyring=keyring, gateway=gateway, clock=clock, signals=signals
        )


@pytest.fixture
async def client(harness: Harness) -> AsyncClient:
    return harness.http


def later(clock: FakeClock, seconds: float) -> datetime:
    return clock.now() + timedelta(seconds=seconds)
