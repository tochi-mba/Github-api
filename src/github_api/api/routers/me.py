"""Who the connected GitHub account is: the hub's probe for "connected"."""

from __future__ import annotations

from fastapi import APIRouter

from github_api.api.dependencies import AccessDep, CallerDep
from github_api.github.models import Identity

router = APIRouter(prefix="/v1", tags=["me"])


@router.get("/me", response_model=Identity)
async def get_me(caller: CallerDep, access: AccessDep) -> Identity:
    """The login, how it is connected (``app`` or ``pat``) and how many repositories it reaches.

    A missing connection is ``502 credential-missing``, which is what tells the hub to offer
    the person a link to connect GitHub.
    """
    return await access.run(caller, access.gateway.me)
