"""An in-memory GitHub, so the routes and the poller are tested without a network.

It satisfies :class:`~github_api.github.protocols.GitHubGateway` (mypy checks it in the test
suite), answers in the same public models the real client maps to, and refuses the way GitHub
refuses: a credential it was told to reject is a 401, a repository it was told to hide is a
404, and any method can be scripted to raise.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from github_api.github.errors import (
    NOT_FOUND,
    GitHubConflictError,
    GitHubNotFoundError,
    GitHubUnauthorizedError,
)
from github_api.github.models import (
    Branch,
    Cancelled,
    ChangedFile,
    ChangedFilePage,
    Checks,
    Commented,
    Committed,
    Dispatched,
    FileExcerpt,
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
    Review,
    Reviewed,
    RunStatus,
    Tree,
    TreeEntry,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from github_api.github.models import GitHubCredential

FAKE_SHA = "0" * 39 + "1"


class FakeGateway:
    """Repositories, pull requests, issues, checks, logs and files, all in memory."""

    def __init__(self, login: str = "octo") -> None:
        self.identity = Identity(login=login, kind="app", selection="selected", repositories=0)
        self.repos: dict[str, Repo] = {}
        self.hidden: set[str] = set()
        self.pull_requests: dict[tuple[str, int], PullDetail] = {}
        self.reviews: dict[tuple[str, int], list[Review]] = {}
        self.changed: dict[tuple[str, int], list[ChangedFile]] = {}
        self.issue_list: dict[tuple[str, int], Issue] = {}
        self.check_runs: dict[tuple[str, str], Checks] = {}
        self.runs: dict[tuple[str, str], RunStatus] = {}
        self.logs: dict[tuple[str, str], str] = {}
        self.files: dict[tuple[str, str], str] = {}
        self.branches: dict[tuple[str, str], str] = {}
        self.healthy: tuple[bool, str | None] = (True, None)
        self.rejected: set[str] = set()
        """``Authorization`` values GitHub answers 401 for."""
        self.refuse: dict[str, Exception] = {}
        """Method name to the error that method raises."""
        self.calls: list[tuple[str, str]] = []
        """(method, the ``Authorization`` it was called with), in order."""
        self.closed = False

    # ------------------------------------------------------------------ seeding

    def seed_repo(self, repo: Repo) -> None:
        self.repos[repo.full_name] = repo
        self.branches[(repo.full_name, repo.default_branch or "main")] = FAKE_SHA

    def seed_pull(self, detail: PullDetail) -> None:
        self.pull_requests[(detail.pull.repo, detail.pull.number)] = detail

    # ------------------------------------------------------------------ plumbing

    def _enter(self, method: str, cred: GitHubCredential, repo: str | None = None) -> None:
        authorization = cred.headers.get("Authorization", "")
        self.calls.append((method, authorization))
        if authorization in self.rejected:
            raise GitHubUnauthorizedError(method)
        if method in self.refuse:
            raise self.refuse[method]
        if repo is not None and (repo in self.hidden or repo not in self.repos):
            raise GitHubNotFoundError(NOT_FOUND)

    def _pull(self, repo: str, number: int) -> PullDetail:
        detail = self.pull_requests.get((repo, number))
        if detail is None:
            raise GitHubNotFoundError(f"{repo}#{number}")
        return detail

    async def aclose(self) -> None:
        self.closed = True

    async def ready(self) -> tuple[bool, str | None]:
        return self.healthy

    async def visible(self, cred: GitHubCredential, repo: str) -> bool:
        self.calls.append(("visible", cred.headers.get("Authorization", "")))
        return repo in self.repos and repo not in self.hidden

    # ------------------------------------------------------------------ reads

    async def me(self, cred: GitHubCredential) -> Identity:
        self._enter("me", cred)
        return self.identity.model_copy(update={"repositories": len(self.repos)})

    async def find_repos(
        self, cred: GitHubCredential, *, query: str, owner: str, limit: int
    ) -> RepoPage:
        self._enter("find_repos", cred)
        found = [
            repo
            for name, repo in sorted(self.repos.items())
            if query in name and name.startswith(f"{owner}/" if owner else "")
        ]
        return RepoPage(repos=found[:limit], total=len(found))

    async def repo(self, cred: GitHubCredential, repo: str) -> Repo:
        self._enter("repo", cred, repo)
        return self.repos[repo]

    async def pulls(self, cred: GitHubCredential, repo: str, *, state: str, limit: int) -> PullPage:
        self._enter("pulls", cred, repo)
        found = [
            detail.pull
            for (name, _), detail in sorted(self.pull_requests.items())
            if name == repo and state in {"all", detail.pull.state}
        ]
        return PullPage(pulls=found[:limit], total=len(found))

    async def pull(self, cred: GitHubCredential, repo: str, number: int) -> PullDetail:
        self._enter("pull", cred, repo)
        return self._pull(repo, number)

    async def changes(
        self, cred: GitHubCredential, repo: str, number: int, *, limit: int
    ) -> ChangedFilePage:
        self._enter("changes", cred, repo)
        self._pull(repo, number)
        files = self.changed.get((repo, number), [])
        return ChangedFilePage(files=files[:limit], total=len(files))

    async def pull_summary(self, cred: GitHubCredential, repo: str, number: int) -> Pull:
        self._enter("pull_summary", cred, repo)
        return self._pull(repo, number).pull

    async def latest_review(self, cred: GitHubCredential, repo: str, number: int) -> LatestReview:
        self._enter("latest_review", cred, repo)
        self._pull(repo, number)
        reviews = self.reviews.get((repo, number), [])
        return LatestReview(total=len(reviews), latest=reviews[-1] if reviews else None)

    async def issues(
        self, cred: GitHubCredential, repo: str, *, state: str, limit: int
    ) -> IssuePage:
        self._enter("issues", cred, repo)
        found = [
            issue
            for (name, _), issue in sorted(self.issue_list.items())
            if name == repo and state in {"all", issue.state}
        ]
        return IssuePage(issues=found[:limit], total=len(found))

    async def checks(self, cred: GitHubCredential, repo: str, ref: str) -> Checks:
        self._enter("checks", cred, repo)
        return self.check_runs.get((repo, ref), Checks(summary="none"))

    async def raw_log(self, cred: GitHubCredential, repo: str, job_id: str) -> str:
        self._enter("raw_log", cred, repo)
        if (repo, job_id) not in self.logs:
            raise GitHubNotFoundError(job_id)
        return self.logs[(repo, job_id)]

    async def run_status(self, cred: GitHubCredential, repo: str, run_id: str) -> RunStatus:
        self._enter("run_status", cred, repo)
        if (repo, run_id) not in self.runs:
            raise GitHubNotFoundError(run_id)
        return self.runs[(repo, run_id)]

    async def read_file(
        self, cred: GitHubCredential, repo: str, path: str, *, ref: str
    ) -> FileExcerpt:
        self._enter("read_file", cred, repo)
        if (repo, path) not in self.files:
            raise GitHubNotFoundError(path)
        content = self.files[(repo, path)]
        return FileExcerpt(
            path=path,
            ref=ref or self.repos[repo].default_branch,
            text=content,
            shown=len(content),
            total=len(content),
            truncated=False,
            binary=False,
        )

    async def tree(self, cred: GitHubCredential, repo: str, path: str, *, ref: str) -> Tree:
        self._enter("tree", cred, repo)
        prefix = f"{path.strip('/')}/" if path else ""
        entries = [
            TreeEntry(path=name, type="file", size=len(body))
            for (owner, name), body in sorted(self.files.items())
            if owner == repo and name.startswith(prefix)
        ]
        return Tree(entries=entries, total=len(entries))

    # ------------------------------------------------------------------ writes

    async def create_repo(
        self, cred: GitHubCredential, *, name: str, owner: str, visibility: str, description: str
    ) -> Repo:
        self._enter("create_repo", cred)
        full_name = f"{owner or self.identity.login}/{name}"
        if full_name in self.repos:
            raise GitHubConflictError(f"{full_name} already exists")
        repo = Repo(
            full_name=full_name,
            private=visibility != "public",
            default_branch="main",
            description=description,
        )
        self.seed_repo(repo)
        return repo

    async def update_repo(
        self, cred: GitHubCredential, repo: str, changes: Mapping[str, Any]
    ) -> Repo:
        self._enter("update_repo", cred, repo)
        update = dict(changes)
        if "visibility" in update:
            update["private"] = update.pop("visibility") != "public"
        self.repos[repo] = self.repos[repo].model_copy(update=update)
        return self.repos[repo]

    async def delete_repo(self, cred: GitHubCredential, repo: str) -> None:
        self._enter("delete_repo", cred, repo)
        del self.repos[repo]

    async def open_pull(self, cred: GitHubCredential, repo: str, fields: Mapping[str, Any]) -> Pull:
        self._enter("open_pull", cred, repo)
        number = 1 + max((n for (name, n) in self.pull_requests if name == repo), default=0)
        pull = Pull(
            repo=repo,
            number=number,
            title=str(fields["title"]),
            state="open",
            author=self.identity.login,
            draft=bool(fields.get("draft", False)),
            head=str(fields["head"]),
            base=str(fields.get("base") or self.repos[repo].default_branch),
        )
        self.seed_pull(PullDetail(pull=pull, body=str(fields.get("body") or "")))
        return pull

    async def update_pull(
        self, cred: GitHubCredential, repo: str, number: int, changes: Mapping[str, Any]
    ) -> Pull:
        self._enter("update_pull", cred, repo)
        detail = self._pull(repo, number)
        pull = detail.pull.model_copy(
            update={key: value for key, value in changes.items() if key != "body"}
        )
        self.seed_pull(detail.model_copy(update={"pull": pull}))
        return pull

    async def merge(
        self, cred: GitHubCredential, repo: str, number: int, *, method: str, delete_branch: bool
    ) -> Merged:
        self._enter("merge", cred, repo)
        detail = self._pull(repo, number)
        if detail.pull.mergeable == "dirty":
            message = "Pull Request is not mergeable"
            raise GitHubConflictError(message)
        self.seed_pull(
            detail.model_copy(update={"pull": detail.pull.model_copy(update={"state": "merged"})})
        )
        if delete_branch:
            self.branches.pop((repo, detail.pull.head), None)
        return Merged(merged=True, sha=FAKE_SHA, message=f"merged by {method}")

    async def review(
        self, cred: GitHubCredential, repo: str, number: int, *, event: str, body: str
    ) -> Reviewed:
        self._enter("review", cred, repo)
        self._pull(repo, number)
        state = {"approve": "approved", "request_changes": "changes_requested"}.get(
            event, "commented"
        )
        self.reviews.setdefault((repo, number), []).append(
            Review(author=self.identity.login, state=state, body=body)
        )
        return Reviewed(state=state, url=f"https://github.test/{repo}/pull/{number}")

    async def comment(self, cred: GitHubCredential, repo: str, number: int, body: str) -> Commented:
        self._enter("comment", cred, repo)
        return Commented(url=f"https://github.test/{repo}/issues/{number}#comment")

    async def open_issue(
        self, cred: GitHubCredential, repo: str, *, title: str, body: str, labels: Sequence[str]
    ) -> Issue:
        self._enter("open_issue", cred, repo)
        number = 1 + max((n for (name, n) in self.issue_list if name == repo), default=0)
        issue = Issue(
            repo=repo,
            number=number,
            title=title,
            state="open",
            author=self.identity.login,
            labels=list(labels),
        )
        self.issue_list[(repo, number)] = issue
        return issue

    async def set_issue_state(
        self, cred: GitHubCredential, repo: str, number: int, state: str
    ) -> Issue:
        self._enter("set_issue_state", cred, repo)
        if (repo, number) not in self.issue_list:
            raise GitHubNotFoundError(f"{repo}#{number}")
        issue = self.issue_list[(repo, number)].model_copy(update={"state": state})
        self.issue_list[(repo, number)] = issue
        return issue

    async def rerun(
        self, cred: GitHubCredential, repo: str, run_id: str, *, failed_only: bool
    ) -> Queued:
        self._enter("rerun", cred, repo)
        return Queued(queued=True)

    async def cancel_run(self, cred: GitHubCredential, repo: str, run_id: str) -> Cancelled:
        self._enter("cancel_run", cred, repo)
        return Cancelled(cancelled=True)

    async def dispatch(
        self,
        cred: GitHubCredential,
        repo: str,
        workflow: str,
        *,
        ref: str,
        inputs: Mapping[str, str],
    ) -> Dispatched:
        self._enter("dispatch", cred, repo)
        return Dispatched(dispatched=True)

    async def commit(  # noqa: PLR0913 - the protocol's shape
        self,
        cred: GitHubCredential,
        repo: str,
        *,
        branch: str,
        message: str,
        files: Sequence[Mapping[str, str]],
        base: str,
    ) -> Committed:
        self._enter("commit", cred, repo)
        if (repo, branch) not in self.branches:
            await self.create_branch(cred, repo, branch, start=base)
        for one in files:
            self.files[(repo, one["path"])] = one["content"]
        return Committed(sha=FAKE_SHA, branch=branch, url=f"https://github.test/{repo}/commit")

    async def create_branch(
        self, cred: GitHubCredential, repo: str, name: str, *, start: str
    ) -> Branch:
        self._enter("create_branch", cred, repo)
        if (repo, name) in self.branches:
            raise GitHubConflictError(f"Reference {name} already exists")
        origin = start or self.repos[repo].default_branch
        if (repo, origin) not in self.branches:
            raise GitHubNotFoundError(f"no branch `{origin}`")
        self.branches[(repo, name)] = self.branches[(repo, origin)]
        return Branch(name=name, sha=self.branches[(repo, name)])

    async def delete_branch(self, cred: GitHubCredential, repo: str, name: str) -> None:
        self._enter("delete_branch", cred, repo)
        if self.branches.pop((repo, name), None) is None:
            raise GitHubNotFoundError(f"no branch `{name}`")


__all__ = ["FAKE_SHA", "FakeGateway"]
