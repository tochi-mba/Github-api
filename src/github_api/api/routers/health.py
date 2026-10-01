"""Liveness and readiness."""

from __future__ import annotations

from fastapi import APIRouter, Response, status

from github_api import __version__
from github_api.api.dependencies import ContainerDep
from github_api.api.schemas import CheckResult, LivenessResponse, ReadyResponse

router = APIRouter(tags=["health"])

STATUS_OK = "ok"
STATUS_DEGRADED = "degraded"


@router.get("/healthy", response_model=LivenessResponse)
async def get_health(container: ContainerDep) -> LivenessResponse:
    """Liveness only: no I/O, never fails."""
    return LivenessResponse(
        status="alive",
        version=__version__,
        environment=container.settings.environment,
        uptime_seconds=round(container.uptime_seconds, 3),
    )


def _check(usable: bool, reason: str | None) -> CheckResult:
    return CheckResult(
        status=STATUS_OK if usable else STATUS_DEGRADED,
        detail={"reachable": usable, "reason": reason},
    )


@router.get(
    "/ready",
    response_model=ReadyResponse,
    responses={status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ReadyResponse}},
)
async def check_readiness(container: ContainerDep, response: Response) -> ReadyResponse:
    """Keyring's signing keys, GitHub's unauthenticated ``/rate_limit``, and the database."""
    keys_usable, keys_reason = await container.jwks.healthy()
    github_usable, github_reason = await container.gateway.ready()
    database = container.store.healthy()
    checks = {
        "keyring": _check(keys_usable, keys_reason),
        "github": _check(github_usable, github_reason),
        "database": _check(database, None if database else "the database cannot be read"),
    }
    healthy = all(check.status == STATUS_OK for check in checks.values())
    if not healthy:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return ReadyResponse(
        status=STATUS_OK if healthy else STATUS_DEGRADED,
        version=__version__,
        environment=container.settings.environment,
        uptime_seconds=round(container.uptime_seconds, 3),
        checks=checks,
    )
