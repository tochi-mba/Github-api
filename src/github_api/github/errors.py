"""Every way GitHub can say no, named for what the caller should do about it.

The HTTP surface turns each into one RFC 9457 problem with a stable ``code``. The names match
the hub's vocabulary (``lucy_api/clients/errors.py``): 404 is absence, 409 a state, 422 a
request that cannot be honoured as sent, 429 an interval to wait.
"""

from __future__ import annotations

NOT_FOUND = "GitHub has no such thing that this connection can see"
"""The one sentence for every absence, so a hidden repository and a missing one read alike."""


class GitHubError(Exception):
    """Base class. ``str(exc)`` is a sentence a person can act on."""


class GitHubUnauthorizedError(GitHubError):
    """GitHub refused the credential (401). The caller may refresh it once."""


class GitHubForbiddenError(GitHubError):
    """The credential does not allow this (403 that is not a rate limit)."""


class GitHubNotFoundError(GitHubError):
    """No such thing, or none this credential may see. The two are one answer."""


class GitHubConflictError(GitHubError):
    """It cannot happen in the state things are in: not mergeable, already exists."""


class GitHubUnprocessableError(GitHubError):
    """GitHub validated the request and refused it; the message says why."""


class GitHubRateLimitedError(GitHubError):
    """Too many requests. ``retry_after`` is GitHub's interval, in whole seconds."""

    def __init__(self, message: str, retry_after: int) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class GitHubUnavailableError(GitHubError):
    """GitHub is down, slow or answered something unreadable."""


__all__ = [
    "NOT_FOUND",
    "GitHubConflictError",
    "GitHubError",
    "GitHubForbiddenError",
    "GitHubNotFoundError",
    "GitHubRateLimitedError",
    "GitHubUnauthorizedError",
    "GitHubUnavailableError",
    "GitHubUnprocessableError",
]
