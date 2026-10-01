"""FastAPI dependency wiring: who is calling, on which profile, and with what."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Header, Path, Query, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from github_api.access import Caller, GitHubAccess
from github_api.auth.verifier import AuthenticationError
from github_api.container import Container
from github_api.jobs.service import Subscriptions

bearer_scheme = HTTPBearer(auto_error=False)

MISSING_CREDENTIALS = "a keyring token for audience github-api is required"
PROFILE_HEADER = "X-Keyring-Profile"
DEFAULT_PROFILE = "default"
PROFILE_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$"

OWNER = r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$"
NAME = r"^[A-Za-z0-9._-]{1,100}$"
REPO = r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})/[A-Za-z0-9._-]{1,100}$"

DEFAULT_LIMIT = 20
MAX_LIMIT = 100


def get_container(request: Request) -> Container:
    container: Container = request.app.state.container
    return container


ContainerDep = Annotated[Container, Depends(get_container)]


async def get_caller(
    container: ContainerDep,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    profile: Annotated[
        str,
        Header(
            alias=PROFILE_HEADER,
            pattern=PROFILE_PATTERN,
            description="Which keyring profile's GitHub connection to use.",
        ),
    ] = DEFAULT_PROFILE,
) -> Caller:
    """The verified caller, the token they presented, and the profile they named.

    The token is kept only to resolve the caller's GitHub credential from keyring, which
    takes the account from it; it is never forwarded to GitHub or anywhere else.
    """
    if credentials is None:
        raise AuthenticationError(MISSING_CREDENTIALS)
    verified = await container.verifier.verify(credentials.credentials)
    return Caller(account_id=verified.account_id, token=credentials.credentials, profile=profile)


CallerDep = Annotated[Caller, Depends(get_caller)]


def get_access(container: ContainerDep) -> GitHubAccess:
    return container.access


AccessDep = Annotated[GitHubAccess, Depends(get_access)]


def get_subscriptions(container: ContainerDep) -> Subscriptions:
    return container.subscriptions


SubscriptionsDep = Annotated[Subscriptions, Depends(get_subscriptions)]

Owner = Annotated[str, Path(pattern=OWNER, description="The repository's owner.")]
Name = Annotated[str, Path(pattern=NAME, description="The repository's name.")]
Number = Annotated[int, Path(ge=1, description="A pull request or issue number.")]
Limit = Annotated[int, Query(ge=1, le=MAX_LIMIT, description="At most this many.")]
