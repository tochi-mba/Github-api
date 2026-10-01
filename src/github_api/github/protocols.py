"""The seam between this service and GitHub.

Everything above this line speaks in the public models; everything below it speaks GitHub.
:class:`~github_api.github.client.GitHubClient` is the real implementation and
:class:`~github_api.github.fake.FakeGateway` the in-memory one the tests use, and both
satisfy :class:`GitHubGateway`, which mypy checks.

Every method takes the caller's :class:`GitHubCredential` first: the gateway holds no
credential of its own and never chooses one.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from github_api.github.models import (
        Branch,
        Cancelled,
        ChangedFilePage,
        Checks,
        Commented,
        Committed,
        Dispatched,
        FileExcerpt,
        GitHubCredential,
        Identity,
        Issue,
        IssuePage,
        LatestReview,
        Merged,
        Pull,
        PullDetail,
        PullPage,
        Queued,
        Repo,
        RepoPage,
        Reviewed,
        RunStatus,
        Tree,
    )


class GitHubGateway(Protocol):
    """Every GitHub operation the routes and the poller use."""

    async def ready(self) -> tuple[bool, str | None]:
        """Whether GitHub answers an unauthenticated ``/rate_limit``, and why not."""
        ...

    async def visible(self, cred: GitHubCredential, repo: str) -> bool:
        """Whether this credential can see ``owner/name`` at all."""
        ...

    async def me(self, cred: GitHubCredential) -> Identity: ...

    async def find_repos(
        self, cred: GitHubCredential, *, query: str, owner: str, limit: int
    ) -> RepoPage: ...

    async def repo(self, cred: GitHubCredential, repo: str) -> Repo: ...

    async def create_repo(
        self, cred: GitHubCredential, *, name: str, owner: str, visibility: str, description: str
    ) -> Repo: ...

    async def update_repo(
        self, cred: GitHubCredential, repo: str, changes: Mapping[str, Any]
    ) -> Repo: ...

    async def delete_repo(self, cred: GitHubCredential, repo: str) -> None: ...

    async def pulls(
        self, cred: GitHubCredential, repo: str, *, state: str, limit: int
    ) -> PullPage: ...

    async def pull(self, cred: GitHubCredential, repo: str, number: int) -> PullDetail: ...

    async def changes(
        self, cred: GitHubCredential, repo: str, number: int, *, limit: int
    ) -> ChangedFilePage: ...

    async def pull_summary(self, cred: GitHubCredential, repo: str, number: int) -> Pull: ...

    async def open_pull(
        self, cred: GitHubCredential, repo: str, fields: Mapping[str, Any]
    ) -> Pull: ...

    async def update_pull(
        self, cred: GitHubCredential, repo: str, number: int, changes: Mapping[str, Any]
    ) -> Pull: ...

    async def merge(
        self, cred: GitHubCredential, repo: str, number: int, *, method: str, delete_branch: bool
    ) -> Merged: ...

    async def review(
        self, cred: GitHubCredential, repo: str, number: int, *, event: str, body: str
    ) -> Reviewed: ...

    async def latest_review(
        self, cred: GitHubCredential, repo: str, number: int
    ) -> LatestReview: ...

    async def comment(
        self, cred: GitHubCredential, repo: str, number: int, body: str
    ) -> Commented: ...

    async def issues(
        self, cred: GitHubCredential, repo: str, *, state: str, limit: int
    ) -> IssuePage: ...

    async def open_issue(
        self, cred: GitHubCredential, repo: str, *, title: str, body: str, labels: Sequence[str]
    ) -> Issue: ...

    async def set_issue_state(
        self, cred: GitHubCredential, repo: str, number: int, state: str
    ) -> Issue: ...

    async def checks(self, cred: GitHubCredential, repo: str, ref: str) -> Checks: ...

    async def raw_log(self, cred: GitHubCredential, repo: str, job_id: str) -> str:
        """The whole log of one CI job, as text. Windowing is the caller's."""
        ...

    async def run_status(self, cred: GitHubCredential, repo: str, run_id: str) -> RunStatus: ...

    async def rerun(
        self, cred: GitHubCredential, repo: str, run_id: str, *, failed_only: bool
    ) -> Queued: ...

    async def cancel_run(self, cred: GitHubCredential, repo: str, run_id: str) -> Cancelled: ...

    async def dispatch(
        self,
        cred: GitHubCredential,
        repo: str,
        workflow: str,
        *,
        ref: str,
        inputs: Mapping[str, str],
    ) -> Dispatched: ...

    async def read_file(
        self, cred: GitHubCredential, repo: str, path: str, *, ref: str
    ) -> FileExcerpt: ...

    async def tree(self, cred: GitHubCredential, repo: str, path: str, *, ref: str) -> Tree: ...

    async def commit(  # noqa: PLR0913 - one commit: where, on which branch, what, why, from what
        self,
        cred: GitHubCredential,
        repo: str,
        *,
        branch: str,
        message: str,
        files: Sequence[Mapping[str, str]],
        base: str,
    ) -> Committed: ...

    async def create_branch(
        self, cred: GitHubCredential, repo: str, name: str, *, start: str
    ) -> Branch: ...

    async def delete_branch(self, cred: GitHubCredential, repo: str, name: str) -> None: ...

    async def aclose(self) -> None: ...


__all__ = ["GitHubGateway"]
