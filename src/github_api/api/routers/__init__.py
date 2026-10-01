"""HTTP routers."""

from __future__ import annotations

from github_api.api.routers import (
    checks,
    contents,
    health,
    issues,
    me,
    pulls,
    repos,
    subscriptions,
)

ROUTERS = (
    health.router,
    me.router,
    repos.router,
    pulls.router,
    issues.router,
    checks.router,
    contents.router,
    subscriptions.router,
)

__all__ = ["ROUTERS"]
