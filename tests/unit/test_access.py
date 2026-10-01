"""The access rules every route shares: one refresh on a 401, and 403 masked as 404."""

from __future__ import annotations

import pytest

from github_api.access import Caller, GitHubAccess
from github_api.credentials.keyring import CredentialUnusableError
from github_api.github.errors import GitHubForbiddenError, GitHubNotFoundError
from github_api.github.fake import FakeGateway
from github_api.github.models import GitHubCredential, Identity, Repo

CALLER = Caller(account_id="acct", token="user-token", profile="work")


class Credentials:
    """Hands out a new credential on every forced resolve."""

    def __init__(self) -> None:
        self.issued = 0
        self.forgotten: list[tuple[str, str]] = []

    async def resolve(
        self, user_token: str, profile: str, *, force: bool = False
    ) -> GitHubCredential:
        self.issued += 1 if force or not self.issued else 0
        return GitHubCredential(headers={"Authorization": f"Bearer ghu_{self.issued}"})

    def forget(self, user_token: str, profile: str) -> None:
        self.forgotten.append((user_token, profile))


@pytest.fixture
def gateway() -> FakeGateway:
    found = FakeGateway()
    found.seed_repo(Repo(full_name="octo/hello"))
    return found


async def test_one_401_is_retried_under_a_forced_credential(gateway: FakeGateway) -> None:
    credentials = Credentials()
    access = GitHubAccess(credentials, gateway)
    gateway.rejected.add("Bearer ghu_1")
    identity = await access.run(CALLER, gateway.me)
    assert isinstance(identity, Identity)
    assert [auth for _, auth in gateway.calls] == ["Bearer ghu_1", "Bearer ghu_2"]


async def test_two_401s_forget_the_credential_and_say_reconnect(gateway: FakeGateway) -> None:
    credentials = Credentials()
    access = GitHubAccess(credentials, gateway)
    gateway.rejected.update({"Bearer ghu_1", "Bearer ghu_2"})
    with pytest.raises(CredentialUnusableError, match="reconnect GitHub"):
        await access.run(CALLER, gateway.me)
    assert credentials.forgotten == [("user-token", "work")]


async def test_a_403_on_a_hidden_repository_is_a_404(gateway: FakeGateway) -> None:
    access = GitHubAccess(Credentials(), gateway)
    gateway.refuse["repo"] = GitHubForbiddenError("Resource not accessible by integration")
    gateway.hidden.add("octo/hello")
    with pytest.raises(GitHubNotFoundError):
        await access.run(CALLER, lambda cred: gateway.repo(cred, "octo/hello"), repo="octo/hello")


async def test_a_403_on_a_visible_repository_or_none_stays_403(gateway: FakeGateway) -> None:
    access = GitHubAccess(Credentials(), gateway)
    gateway.refuse["repo"] = GitHubForbiddenError("needs Administration")
    with pytest.raises(GitHubForbiddenError):
        await access.run(CALLER, lambda cred: gateway.repo(cred, "octo/hello"), repo="octo/hello")
    gateway.refuse["me"] = GitHubForbiddenError("no")
    with pytest.raises(GitHubForbiddenError):
        await access.run(CALLER, gateway.me)


def test_a_caller_never_shows_its_token() -> None:
    assert "user-token" not in repr(CALLER)
