"""One GitHub operation, for one caller: their credential, one refresh, and no leaks.

Every route goes through :meth:`GitHubAccess.run`, so three rules hold everywhere:

1. **The credential is the caller's**, resolved from keyring for the token they presented and
   the profile they named, and cached per ``profile:sha256(token)``.
2. **A 401 from GitHub earns one forced refresh.** Keyring renews an expiring GitHub user
   token on resolve, so asking again can help. A second 401 means the stored grant is dead:
   the caller gets ``502 credential-unavailable``, which the hub turns into "reconnect".
3. **A repository the credential cannot see is a 404, never a 403.** GitHub sometimes answers
   403 for a repository outside an app installation; telling that apart from "no such
   repository" would confirm that a private repository exists. So a 403 on a repository is
   checked against visibility first, and only a visible repository's 403 is reported as one.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from github_api.credentials.keyring import CredentialUnusableError
from github_api.github.errors import (
    NOT_FOUND,
    GitHubForbiddenError,
    GitHubNotFoundError,
    GitHubUnauthorizedError,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from github_api.github.models import GitHubCredential
    from github_api.github.protocols import GitHubGateway

REFUSED_TWICE = "GitHub refused the stored credential even after it was refreshed; reconnect GitHub"


@dataclass(frozen=True, slots=True, repr=False)
class Caller:
    """Who is asking, with the token they presented and the profile they named."""

    account_id: str
    token: str
    profile: str

    def __repr__(self) -> str:
        """The token is a credential; it is never shown."""
        return f"Caller(account_id={self.account_id!r}, profile={self.profile!r})"


class CredentialSource(Protocol):
    async def resolve(
        self, user_token: str, profile: str, *, force: bool = False
    ) -> GitHubCredential: ...

    def forget(self, user_token: str, profile: str) -> None: ...


class GitHubAccess:
    """Runs gateway operations under the caller's credential."""

    def __init__(self, credentials: CredentialSource, gateway: GitHubGateway) -> None:
        self._credentials = credentials
        self.gateway = gateway

    async def credential(self, caller: Caller, *, force: bool = False) -> GitHubCredential:
        return await self._credentials.resolve(caller.token, caller.profile, force=force)

    async def run[T](
        self,
        caller: Caller,
        operation: Callable[[GitHubCredential], Awaitable[T]],
        *,
        repo: str | None = None,
    ) -> T:
        """``operation`` under the caller's credential, with the rules above.

        Raises:
            CredentialError: no usable credential (including one GitHub refused twice).
            GitHubError: whatever GitHub said, with a hidden repository's 403 as a 404.
        """
        cred = await self.credential(caller)
        try:
            return await self._attempt(operation, cred, repo)
        except GitHubUnauthorizedError:
            cred = await self.credential(caller, force=True)
        try:
            return await self._attempt(operation, cred, repo)
        except GitHubUnauthorizedError as exc:
            self._credentials.forget(caller.token, caller.profile)
            raise CredentialUnusableError(REFUSED_TWICE) from exc

    async def _attempt[T](
        self,
        operation: Callable[[GitHubCredential], Awaitable[T]],
        cred: GitHubCredential,
        repo: str | None,
    ) -> T:
        try:
            return await operation(cred)
        except GitHubForbiddenError:
            if repo is not None and not await self.gateway.visible(cred, repo):
                raise GitHubNotFoundError(NOT_FOUND) from None
            raise


__all__ = ["Caller", "CredentialSource", "GitHubAccess"]
