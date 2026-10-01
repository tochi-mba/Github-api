"""One way to talk to GitHub: credentials attached, rate limits honoured, failures named.

Everything that touches ``httpx`` for GitHub is here, so the rules are written once:

* **The caller's credential, or none.** Headers come from keyring per request; this module
  never holds one beyond the call, and redirects are never followed with one attached (a log
  download is a redirect to blob storage, which must not see the token).
* **``X-RateLimit-*`` is obeyed.** When GitHub says a bucket is empty, further calls for the
  same credential and bucket are refused here, with GitHub's own reset as ``Retry-After``,
  instead of spending a request to be told again.
* **Every failure is one of** :mod:`github_api.github.errors`, chosen from the status and
  GitHub's message, so the layers above never see an ``httpx`` exception.
"""

from __future__ import annotations

import hashlib
import math
import time
from typing import TYPE_CHECKING, Any

import httpx

from github_api.github.errors import (
    NOT_FOUND,
    GitHubConflictError,
    GitHubError,
    GitHubForbiddenError,
    GitHubNotFoundError,
    GitHubRateLimitedError,
    GitHubUnauthorizedError,
    GitHubUnavailableError,
    GitHubUnprocessableError,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from github_api.github.models import GitHubCredential

API_VERSION = "2022-11-28"
JSON_MEDIA = "application/vnd.github+json"
RAW_MEDIA = "application/vnd.github.raw"
USER_AGENT = "github-api (LUCY family)"

DEFAULT_RETRY_SECONDS = 60
NO_CONTENT = 204
REDIRECTS = frozenset({301, 302, 303, 307, 308})

UNAUTHORIZED = "GitHub did not accept the stored credential"
RATE_LIMITED = "GitHub's rate limit for this connection is spent"
UNREADABLE = "GitHub's answer could not be read"
UNREACHABLE = "GitHub could not be reached"

CONFLICT_PHRASES = ("already exist", "fast forward", "not mergeable", "is not mergeable")
MISSING_PHRASES = ("reference does not exist",)


class GitHubHttp:
    """An ``httpx`` client for GitHub's REST and GraphQL APIs."""

    def __init__(  # noqa: PLR0913 - where, how long, through what, by which clock
        self,
        *,
        base_url: str,
        graphql_url: str,
        timeout_seconds: float,
        transport: httpx.AsyncBaseTransport | None = None,
        download_transport: httpx.AsyncBaseTransport | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        headers = {"Accept": JSON_MEDIA, "X-GitHub-Api-Version": API_VERSION}
        headers["User-Agent"] = USER_AGENT
        self._base = base_url.rstrip("/")
        self._graphql = graphql_url
        self._timeout = timeout_seconds
        self._client = httpx.AsyncClient(
            timeout=timeout_seconds, transport=transport, follow_redirects=False, headers=headers
        )
        # Downloads go to wherever GitHub redirects them, never with a credential.
        self._downloads = httpx.AsyncClient(
            timeout=timeout_seconds,
            transport=download_transport or transport,
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT},
        )
        self._clock = clock
        self._spent: dict[tuple[str, str], float] = {}

    async def aclose(self) -> None:
        await self._client.aclose()
        await self._downloads.aclose()

    # ------------------------------------------------------------------ requests

    async def send(  # noqa: PLR0913 - one request, described
        self,
        method: str,
        path: str,
        *,
        cred: GitHubCredential | None,
        params: Mapping[str, Any] | None = None,
        body: Any = None,
        accept: str | None = None,
    ) -> httpx.Response:
        """One REST call. Returns the response for a 2xx or a redirect; raises otherwise."""
        bucket = "core"
        self._refuse_if_spent(cred, bucket)
        headers = dict(cred.headers) if cred is not None else {}
        if accept is not None:
            headers["Accept"] = accept
        response = await self._exchange(
            method, f"{self._base}{path}", headers=headers, params=params, body=body
        )
        self._note_limits(cred, bucket, response)
        if response.is_success or response.status_code in REDIRECTS:
            return response
        raise self._error_for(response)

    async def json(
        self,
        method: str,
        path: str,
        *,
        cred: GitHubCredential | None,
        params: Mapping[str, Any] | None = None,
        body: Any = None,
    ) -> Any:
        """One REST call, decoded. A ``204`` is ``None``."""
        response = await self.send(method, path, cred=cred, params=params, body=body)
        if response.status_code == NO_CONTENT or not response.content:
            return None
        return _decode(response)

    async def graphql(
        self,
        cred: GitHubCredential,
        query: str,
        variables: Mapping[str, Any],
        *,
        root: Sequence[str],
    ) -> Any:
        """One GraphQL call, returning the object at ``root`` or raising for its absence.

        GraphQL answers 200 with an ``errors`` list for most failures, so the HTTP status
        alone says little. The object the caller asked for being null is what decides:
        nested nulls inside it (a field the token may not read) become defaults in the
        mappers instead of failing the whole read.
        """
        bucket = "graphql"
        self._refuse_if_spent(cred, bucket)
        response = await self._exchange(
            "POST",
            self._graphql,
            headers=dict(cred.headers),
            params=None,
            body={"query": query, "variables": dict(variables)},
        )
        self._note_limits(cred, bucket, response)
        if not response.is_success:
            raise self._error_for(response)
        document = _decode(response)
        found: Any = document.get("data") if isinstance(document, dict) else None
        for key in root:
            found = found.get(key) if isinstance(found, dict) else None
        if found is None:
            errors = document.get("errors") if isinstance(document, dict) else None
            raise self._graphql_error(errors, response)
        return found

    async def download(self, path: str, *, cred: GitHubCredential) -> str:
        """Text behind a GitHub URL that redirects to storage. The token stays with GitHub."""
        response = await self.send("GET", path, cred=cred)
        if response.status_code not in REDIRECTS:
            return response.text
        location = response.headers.get("Location", "")
        try:
            stored = await self._downloads.get(location)
        except httpx.HTTPError as exc:
            raise GitHubUnavailableError(UNREACHABLE) from exc
        if not stored.is_success:
            message = f"GitHub's storage answered {stored.status_code} for the download"
            raise GitHubUnavailableError(message)
        return stored.text

    async def ready(self) -> tuple[bool, str | None]:
        """Whether GitHub answers an unauthenticated ``/rate_limit``."""
        try:
            response = await self._client.get(f"{self._base}/rate_limit")
        except httpx.HTTPError as exc:
            return False, type(exc).__name__
        if response.is_success:
            return True, None
        return False, f"GitHub answered {response.status_code}"

    # ------------------------------------------------------------------ internals

    async def _exchange(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        params: Mapping[str, Any] | None,
        body: Any,
    ) -> httpx.Response:
        try:
            return await self._client.request(
                method, url, headers=dict(headers), params=params, json=body
            )
        except httpx.TimeoutException as exc:
            message = f"GitHub did not answer within {self._timeout:g}s"
            raise GitHubUnavailableError(message) from exc
        except httpx.HTTPError as exc:
            raise GitHubUnavailableError(UNREACHABLE) from exc

    def _refuse_if_spent(self, cred: GitHubCredential | None, bucket: str) -> None:
        key = (_fingerprint(cred), bucket)
        reset = self._spent.get(key)
        if reset is None:
            return
        wait = reset - self._clock()
        if wait > 0:
            raise GitHubRateLimitedError(RATE_LIMITED, math.ceil(wait))
        del self._spent[key]

    def _note_limits(
        self, cred: GitHubCredential | None, bucket: str, response: httpx.Response
    ) -> None:
        if response.headers.get("X-RateLimit-Remaining") != "0":
            return
        reset = _number(response.headers.get("X-RateLimit-Reset"))
        if reset is not None:
            self._spent[(_fingerprint(cred), bucket)] = reset

    def _retry_after(self, response: httpx.Response) -> int:
        stated = _number(response.headers.get("Retry-After"))
        if stated is not None:
            return max(1, math.ceil(stated))
        reset = _number(response.headers.get("X-RateLimit-Reset"))
        if reset is not None:
            return max(1, math.ceil(reset - self._clock()))
        return DEFAULT_RETRY_SECONDS

    def _error_for(self, response: httpx.Response) -> Exception:
        status = response.status_code
        message = _message(response)
        if status == httpx.codes.UNAUTHORIZED:
            return GitHubUnauthorizedError(UNAUTHORIZED)
        if status == httpx.codes.TOO_MANY_REQUESTS or (
            status == httpx.codes.FORBIDDEN
            and (
                response.headers.get("X-RateLimit-Remaining") == "0"
                or "rate limit" in message.lower()
                or "Retry-After" in response.headers
            )
        ):
            return GitHubRateLimitedError(RATE_LIMITED, self._retry_after(response))
        return _classify(status, message)

    def _graphql_error(self, errors: Any, response: httpx.Response) -> Exception:
        first = errors[0] if isinstance(errors, list) and errors else {}
        kind = str(first.get("type", "")) if isinstance(first, dict) else ""
        message = str(first.get("message", "")) if isinstance(first, dict) else ""
        if kind == "RATE_LIMITED":
            return GitHubRateLimitedError(RATE_LIMITED, self._retry_after(response))
        if kind == "FORBIDDEN":
            return GitHubForbiddenError(message or "GitHub refused this for the connection")
        if kind in {"", "NOT_FOUND"}:
            return GitHubNotFoundError(NOT_FOUND)
        return GitHubUnprocessableError(message or kind)


STATUS_KINDS: dict[int, tuple[type[GitHubError], str]] = {
    403: (GitHubForbiddenError, "GitHub refused this for the connection"),
    404: (GitHubNotFoundError, NOT_FOUND),
    405: (GitHubConflictError, "GitHub refused this in the current state"),
    409: (GitHubConflictError, "GitHub refused this in the current state"),
    410: (GitHubNotFoundError, NOT_FOUND),
    422: (GitHubUnprocessableError, "GitHub refused the request as sent"),
}


def _classify(status: int, message: str) -> GitHubError:
    """Everything that is not a refused credential or a rate limit."""
    lowered = message.lower()
    if status == httpx.codes.UNPROCESSABLE_ENTITY:
        if any(phrase in lowered for phrase in CONFLICT_PHRASES):
            return GitHubConflictError(message)
        if any(phrase in lowered for phrase in MISSING_PHRASES):
            return GitHubNotFoundError(message)
    if status >= httpx.codes.INTERNAL_SERVER_ERROR:
        return GitHubUnavailableError(f"GitHub answered {status}")
    kind, fallback = STATUS_KINDS.get(
        status, (GitHubUnprocessableError, f"GitHub answered {status}")
    )
    return kind(NOT_FOUND if kind is GitHubNotFoundError else message or fallback)


def _fingerprint(cred: GitHubCredential | None) -> str:
    """Which rate-limit bucket a credential draws from, without keeping the credential."""
    if cred is None:
        return "anonymous"
    joined = "\n".join(f"{k}:{v}" for k, v in sorted(cred.headers.items()))
    return hashlib.sha256(joined.encode()).hexdigest()


def _number(value: str | None) -> float | None:
    try:
        return float(value) if value is not None else None
    except ValueError:
        return None


def _decode(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError as exc:
        raise GitHubUnavailableError(UNREADABLE) from exc


def _message(response: httpx.Response) -> str:
    """GitHub's own ``message``, with each validation error's message appended."""
    try:
        body = response.json()
    except ValueError:
        return ""
    if not isinstance(body, dict):
        return ""
    parts = [str(body.get("message") or "")]
    for error in body.get("errors") or []:
        if isinstance(error, dict) and error.get("message"):
            parts.append(str(error["message"]))
        elif isinstance(error, dict) and error.get("field"):
            parts.append(f"`{error['field']}` is {error.get('code') or 'invalid'}")
        elif isinstance(error, str):
            parts.append(error)
    return ": ".join(part for part in parts if part)


__all__ = ["GitHubHttp"]
