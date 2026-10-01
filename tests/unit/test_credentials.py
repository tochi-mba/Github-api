"""Keyring credentials: cached per profile and token digest, refreshed on demand, named errors."""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

import httpx
import pytest
from keyring_client import CredentialClient
from keyring_client.testing import BASE_URL, FakeClock, FakeKeyring, mint

from github_api.credentials.keyring import (
    CredentialMissingError,
    CredentialUnusableError,
    KeyringCredentials,
    KeyringDownError,
    KeyringRefusedServiceError,
)
from tests.conftest import ACCOUNT, AUDIENCE, PROFILE, SERVICE_TOKEN, connect

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

TOKEN = mint(account_id=ACCOUNT, audience=AUDIENCE)


class Harness:
    def __init__(self, keyring: FakeKeyring, *, service_token: str = SERVICE_TOKEN) -> None:
        self.keyring = keyring
        self.clock = FakeClock()
        self.credentials = KeyringCredentials(
            CredentialClient(
                base_url=BASE_URL, service_token=service_token, transport=keyring.transport()
            ),
            service="github",
            refresh_margin_seconds=60,
            default_seconds=300,
            max_entries=2,
            clock=lambda: self.clock.now().timestamp(),
        )

    @property
    def fetches(self) -> int:
        return len(self.keyring.internal_calls)


@pytest.fixture
async def harness() -> AsyncIterator[Harness]:
    keyring = FakeKeyring(service_tokens={AUDIENCE: SERVICE_TOKEN})
    connect(keyring)
    found = Harness(keyring)
    yield found
    await found.credentials.aclose()


async def test_a_credential_is_cached_until_the_default_lifetime(harness: Harness) -> None:
    first = await harness.credentials.resolve(TOKEN, PROFILE)
    again = await harness.credentials.resolve(TOKEN, PROFILE)
    assert first is again
    assert first.kind == "app"
    assert first.headers == {"Authorization": "Bearer ghu_first"}
    assert harness.fetches == 1
    harness.clock.advance(301)
    await harness.credentials.resolve(TOKEN, PROFILE)
    assert harness.fetches == 2


async def test_force_and_forget_go_back_to_keyring(harness: Harness) -> None:
    await harness.credentials.resolve(TOKEN, PROFILE)
    connect(harness.keyring, "token github_pat_second")
    forced = await harness.credentials.resolve(TOKEN, PROFILE, force=True)
    assert forced.kind == "pat"
    harness.credentials.forget(TOKEN, PROFILE)
    await harness.credentials.resolve(TOKEN, PROFILE)
    assert harness.fetches == 3


async def test_keyrings_expiry_less_the_margin_bounds_the_cache(harness: Harness) -> None:
    connect(harness.keyring, expires_at=harness.clock.now() + timedelta(seconds=120))
    cred = await harness.credentials.resolve(TOKEN, PROFILE)
    assert cred.expires_at == (harness.clock.now() + timedelta(seconds=120)).timestamp()
    harness.clock.advance(61)
    await harness.credentials.resolve(TOKEN, PROFILE)
    assert harness.fetches == 2


async def test_a_credential_inside_the_margin_is_never_cached(harness: Harness) -> None:
    connect(harness.keyring, expires_at=harness.clock.now() + timedelta(seconds=30))
    await harness.credentials.resolve(TOKEN, PROFILE)
    await harness.credentials.resolve(TOKEN, PROFILE)
    assert harness.fetches == 2


async def test_the_cache_is_bounded_and_keyed_by_profile_and_token(harness: Harness) -> None:
    for profile in ("a", "b", "c"):
        connect(harness.keyring, profile=profile)
        await harness.credentials.resolve(TOKEN, profile)
    await harness.credentials.resolve(TOKEN, "c")
    assert harness.fetches == 3
    await harness.credentials.resolve(TOKEN, "a")
    assert harness.fetches == 4
    key = KeyringCredentials.key(TOKEN, "a")
    assert key.startswith("a:")
    assert TOKEN not in key


async def test_keyring_answers_become_named_errors(harness: Harness) -> None:
    with pytest.raises(CredentialMissingError, match="not connected"):
        await harness.credentials.resolve(TOKEN, "nowhere")
    harness.keyring.sealed = True
    with pytest.raises(CredentialUnusableError, match="sealed"):
        await harness.credentials.resolve(TOKEN, PROFILE)
    harness.keyring.error = httpx.ConnectError("down")
    with pytest.raises(KeyringDownError):
        await harness.credentials.resolve(TOKEN, PROFILE)


async def test_a_refused_service_token_is_the_operators() -> None:
    keyring = FakeKeyring(service_tokens={AUDIENCE: SERVICE_TOKEN})
    found = Harness(keyring, service_token="y" * 40)
    with pytest.raises(KeyringRefusedServiceError, match="GHAPI_KEYRING_SERVICE_TOKEN"):
        await found.credentials.resolve(TOKEN, PROFILE)
    await found.credentials.aclose()
