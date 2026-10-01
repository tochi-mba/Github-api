"""The caller's GitHub credential, resolved from keyring and cached briefly.

Keyring will not let this service name a person: it hands back a credential only for the
person whose token came with the request, for the profile they named. So the cache is keyed
by ``profile:sha256(user token)`` -- never by the token itself -- and an entry lives until
shortly before keyring said the credential expires (or a short default when it did not say).

When GitHub refuses a cached credential, the caller asks again with ``force=True``: keyring
renews an expiring GitHub user token on resolve, so a second answer can differ from the
first. Only once; a credential refused twice is reported, not retried.
"""

from __future__ import annotations

import hashlib
from collections import OrderedDict
from typing import TYPE_CHECKING

from keyring_client import (
    CredentialClient,
    CredentialNotFoundError,
    CredentialUnavailableError,
    KeyringRejectedError,
    KeyringUnreachableError,
)

from github_api.github.models import GitHubCredential

if TYPE_CHECKING:
    from collections.abc import Callable

    from keyring_client import ResolvedCredential

APP_TOKEN_PREFIXES = ("ghu_", "gho_")
"""GitHub App user-to-server and OAuth tokens. Anything else is a token the person made."""


class CredentialError(Exception):
    """No usable credential. ``code`` is the problem code the HTTP surface answers with."""

    code = "credential-unavailable"


class CredentialMissingError(CredentialError):
    """The person has not connected GitHub on this profile."""

    code = "credential-missing"


class CredentialUnusableError(CredentialError):
    """Keyring holds a connection that cannot be made to work: sealed, revoked, refused."""

    code = "credential-unavailable"


class KeyringRefusedServiceError(CredentialError):
    """Keyring refused this service's own token. An operator's problem, not the person's."""

    code = "keyring-rejected"


class KeyringDownError(CredentialError):
    """Keyring could not be reached. Nobody's fault yet; a retry may work."""

    code = "keyring-unavailable"


MISSING = "GitHub is not connected on this profile; connect it in keyring"
REFUSED = "keyring refused this service's token; the operator must fix GHAPI_KEYRING_SERVICE_TOKEN"
DOWN = "keyring could not be reached to fetch the GitHub credential"


class KeyringCredentials:
    """Resolves and caches GitHub credentials through keyring's internal surface."""

    def __init__(  # noqa: PLR0913 - keyring, its name for GitHub, and the cache's three knobs
        self,
        client: CredentialClient,
        *,
        service: str,
        refresh_margin_seconds: float,
        default_seconds: float,
        max_entries: int,
        clock: Callable[[], float],
    ) -> None:
        self._client = client
        self._service = service
        self._margin = refresh_margin_seconds
        self._default = default_seconds
        self._max = max_entries
        self._clock = clock
        self._cache: OrderedDict[str, tuple[GitHubCredential, float]] = OrderedDict()

    @staticmethod
    def key(user_token: str, profile: str) -> str:
        """The cache key: the profile, and a digest of the token rather than the token."""
        return f"{profile}:{hashlib.sha256(user_token.encode()).hexdigest()}"

    async def resolve(
        self, user_token: str, profile: str, *, force: bool = False
    ) -> GitHubCredential:
        """The credential for this person and profile; cached unless ``force``.

        Raises:
            CredentialError: a subclass naming who can fix it.
        """
        key = self.key(user_token, profile)
        now = self._clock()
        cached = self._cache.get(key)
        if cached is not None and not force and cached[1] > now:
            self._cache.move_to_end(key)
            return cached[0]
        self._cache.pop(key, None)
        resolved = await self._fetch(user_token, profile)
        credential = _credential(resolved)
        until = now + self._default
        if credential.expires_at is not None:
            until = min(until, credential.expires_at - self._margin)
        if until > now:
            self._cache[key] = (credential, until)
            while len(self._cache) > self._max:
                self._cache.popitem(last=False)
        return credential

    def forget(self, user_token: str, profile: str) -> None:
        """Drop a cached credential GitHub has refused."""
        self._cache.pop(self.key(user_token, profile), None)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _fetch(self, user_token: str, profile: str) -> ResolvedCredential:
        try:
            return await self._client.resolve_credential(
                user_token=user_token, profile=profile, service=self._service
            )
        except CredentialNotFoundError as exc:
            raise CredentialMissingError(MISSING) from exc
        except CredentialUnavailableError as exc:
            raise CredentialUnusableError(str(exc)) from exc
        except KeyringRejectedError as exc:
            raise KeyringRefusedServiceError(REFUSED) from exc
        except KeyringUnreachableError as exc:
            raise KeyringDownError(DOWN) from exc


def _credential(resolved: ResolvedCredential) -> GitHubCredential:
    authorization = resolved.headers.get("Authorization", "")
    token = authorization.rpartition(" ")[2]
    return GitHubCredential(
        headers=dict(resolved.headers),
        kind="app" if token.startswith(APP_TOKEN_PREFIXES) else "pat",
        expires_at=resolved.expires_at.timestamp() if resolved.expires_at else None,
    )


__all__ = [
    "CredentialError",
    "CredentialMissingError",
    "CredentialUnusableError",
    "KeyringCredentials",
    "KeyringDownError",
    "KeyringRefusedServiceError",
]
