"""The fixed public shapes this service answers with.

Every one of them is narrow on purpose. A GitHub pull request arrives with users, avatars,
URLs for every relation and both repositories in full; none of it helps somebody say "#42 is
green and mergeable". The mappers project raw payloads into these and nothing else crosses
the boundary, so a field GitHub starts sending tomorrow cannot reach a caller by accident.

The field names are the hub's contract (`lucy_api/clients/repos.py` in LUCY-assistant):
renaming one here is a breaking change there.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class Public(BaseModel):
    """A frozen projection that refuses fields it does not name."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class GitHubCredential(Public):
    """What to attach to a GitHub request for one person, and when it stops working.

    Never rendered: ``repr`` counts headers rather than showing them, because an
    ``Authorization`` header in a traceback is a credential in a log.
    """

    headers: dict[str, str]
    kind: str = "pat"
    """``app`` for a GitHub App user token, ``pat`` for a token the person made."""
    expires_at: float | None = None
    """Epoch seconds, or ``None`` when keyring did not say."""

    def __repr__(self) -> str:
        """Never show a header value."""
        return f"GitHubCredential(kind={self.kind!r}, headers=<{len(self.headers)} redacted>)"

    __str__ = __repr__


class Identity(Public):
    login: str
    kind: str
    selection: str = ""
    repositories: int = 0


class Repo(Public):
    full_name: str
    private: bool = False
    default_branch: str = ""
    description: str = ""
    open_pulls: int = 0
    open_issues: int = 0
    ci: str = "none"
    url: str = ""


class Pull(Public):
    repo: str
    number: int
    title: str
    state: str = ""
    author: str = ""
    draft: bool = False
    head: str = ""
    base: str = ""
    mergeable: str = "unknown"
    checks: str = "none"
    url: str = ""


class Review(Public):
    author: str
    state: str
    body: str = ""


class Thread(Public):
    path: str
    line: int = 0
    author: str = ""
    body: str = ""
    resolved: bool = False


class CheckRun(Public):
    id: str
    name: str
    status: str = ""
    conclusion: str = ""
    workflow: str = ""
    failing_steps: list[str] = []
    url: str = ""


class PullDetail(Public):
    pull: Pull
    body: str = ""
    reviews: list[Review] = []
    threads: list[Thread] = []
    checks: list[CheckRun] = []


class ChangedFile(Public):
    path: str
    status: str = ""
    additions: int = 0
    deletions: int = 0
    patch: str = ""
    patch_truncated: bool = False
    previous_path: str = ""


class ChangedFilePage(Public):
    files: list[ChangedFile]
    total: int


class Checks(Public):
    summary: str
    checks: list[CheckRun] = []


class Issue(Public):
    repo: str
    number: int
    title: str
    state: str = ""
    author: str = ""
    labels: list[str] = []
    url: str = ""


class RepoPage(Public):
    repos: list[Repo]
    total: int


class PullPage(Public):
    pulls: list[Pull]
    total: int


class IssuePage(Public):
    issues: list[Issue]
    total: int


class TreeEntry(Public):
    path: str
    type: str
    size: int = 0


class Tree(Public):
    entries: list[TreeEntry]
    total: int


class LogExcerpt(Public):
    text: str
    shown: int
    total: int
    truncated: bool


class FileExcerpt(Public):
    path: str
    ref: str
    text: str
    shown: int
    total: int
    truncated: bool
    binary: bool


class Merged(Public):
    merged: bool
    sha: str = ""
    message: str = ""


class Reviewed(Public):
    state: str
    url: str = ""


class Commented(Public):
    url: str


class Committed(Public):
    sha: str
    branch: str
    url: str = ""


class Branch(Public):
    name: str
    sha: str


class Queued(Public):
    queued: bool


class Cancelled(Public):
    cancelled: bool


class Dispatched(Public):
    dispatched: bool


class RunStatus(Public):
    """One CI job (or workflow run): where it stands. Used by subscriptions."""

    id: str
    name: str
    status: str
    conclusion: str = ""
    url: str = ""


class LatestReview(Public):
    """How many reviews a pull request has, and the newest. Used by subscriptions."""

    total: int
    latest: Review | None = None
