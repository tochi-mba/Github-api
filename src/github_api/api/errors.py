"""Every failure as one RFC 9457 problem document with a stable ``code``.

The codes are the hub's vocabulary (``lucy_api/clients/errors.py``): ``not-found`` (404),
``conflict`` (409), ``precondition`` (412), ``unprocessable``/``invalid-request`` (422),
``rate-limited`` (429, with ``Retry-After``), and ``credential-missing`` or
``credential-unavailable`` (502), which the hub reads as "not connected" and turns into a
link instead of an apology. The ``detail`` names the fix.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from github_api.auth.verifier import AuthenticationError, KeyringUnreachableError
from github_api.credentials.keyring import CredentialError, KeyringDownError
from github_api.github.errors import (
    GitHubConflictError,
    GitHubError,
    GitHubForbiddenError,
    GitHubNotFoundError,
    GitHubRateLimitedError,
    GitHubUnprocessableError,
)
from github_api.jobs.models import SubscriptionRefusedError
from github_api.jobs.service import SubscriptionNotFoundError

PROBLEM_JSON = "application/problem+json"
PROBLEM_BASE = "https://github.com/tochi-mba/Github-api/blob/main/docs/api.md#"

TITLES = {
    400: "Bad request",
    401: "Unauthenticated",
    403: "Forbidden",
    404: "Not found",
    405: "Method not allowed",
    409: "Conflict",
    422: "Unprocessable",
    429: "Rate limited",
    502: "Bad gateway",
    503: "Unavailable",
}

GITHUB_STATUS: tuple[tuple[type[GitHubError], int, str], ...] = (
    (GitHubNotFoundError, 404, "not-found"),
    (GitHubForbiddenError, 403, "forbidden"),
    (GitHubConflictError, 409, "conflict"),
    (GitHubUnprocessableError, 422, "unprocessable"),
    (GitHubRateLimitedError, 429, "rate-limited"),
)
"""Anything else from GitHub -- down, slow, unreadable -- is a 502 ``github-unavailable``."""


def problem(
    status: int, code: str, detail: str, *, headers: dict[str, str] | None = None
) -> JSONResponse:
    """One problem document."""
    body: dict[str, Any] = {
        "type": PROBLEM_BASE + code,
        "title": TITLES.get(status, "Error"),
        "status": status,
        "detail": detail,
        "code": code,
    }
    return JSONResponse(status_code=status, content=body, media_type=PROBLEM_JSON, headers=headers)


def _validation_detail(exc: RequestValidationError) -> str:
    parts = []
    for error in exc.errors():
        where = ".".join(str(part) for part in error.get("loc", ()) if part != "body")
        parts.append(f"`{where}`: {error.get('msg', 'invalid')}")
    return "; ".join(parts) or "the request is not valid"


def register_exception_handlers(app: FastAPI) -> None:
    """Install one handler per failure family."""

    @app.exception_handler(AuthenticationError)
    async def _auth(_request: Request, exc: AuthenticationError) -> JSONResponse:
        return problem(401, "unauthenticated", str(exc), headers={"WWW-Authenticate": "Bearer"})

    @app.exception_handler(KeyringUnreachableError)
    async def _keys(_request: Request, exc: KeyringUnreachableError) -> JSONResponse:
        return problem(503, "keyring-unavailable", str(exc))

    @app.exception_handler(CredentialError)
    async def _credential(_request: Request, exc: CredentialError) -> JSONResponse:
        status = 503 if isinstance(exc, KeyringDownError) else 502
        return problem(status, exc.code, str(exc))

    @app.exception_handler(GitHubError)
    async def _github(_request: Request, exc: GitHubError) -> JSONResponse:
        for kind, status, code in GITHUB_STATUS:
            if isinstance(exc, kind):
                headers = None
                if isinstance(exc, GitHubRateLimitedError):
                    headers = {"Retry-After": str(exc.retry_after)}
                return problem(status, code, str(exc), headers=headers)
        return problem(502, "github-unavailable", str(exc))

    @app.exception_handler(SubscriptionRefusedError)
    async def _refused(_request: Request, exc: SubscriptionRefusedError) -> JSONResponse:
        status = 409 if exc.code == "too-many-subscriptions" else 422
        return problem(status, exc.code, str(exc))

    @app.exception_handler(SubscriptionNotFoundError)
    async def _no_subscription(_request: Request, _exc: SubscriptionNotFoundError) -> JSONResponse:
        return problem(404, "not-found", "no such subscription for this account")

    @app.exception_handler(RequestValidationError)
    async def _invalid(_request: Request, exc: RequestValidationError) -> JSONResponse:
        return problem(422, "invalid-request", _validation_detail(exc))

    @app.exception_handler(HTTPException)
    async def _http(_request: Request, exc: HTTPException) -> JSONResponse:
        code = "not-found" if exc.status_code == 404 else "http-error"  # noqa: PLR2004
        return problem(exc.status_code, code, str(exc.detail))


__all__ = ["problem", "register_exception_handlers"]
