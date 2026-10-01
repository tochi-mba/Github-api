"""Repositories: find, read, create, change visibility, delete."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query, Response, status

from github_api.api.dependencies import (
    DEFAULT_LIMIT,
    AccessDep,
    CallerDep,
    Limit,
    Name,
    Owner,
)
from github_api.api.schemas import NewRepo, RepoChanges
from github_api.github.models import Repo, RepoPage

router = APIRouter(prefix="/v1/repos", tags=["repos"])


@router.get("", response_model=RepoPage)
async def find_repos(
    *,
    caller: CallerDep,
    access: AccessDep,
    query: Annotated[str, Query(max_length=256)] = "",
    owner: Annotated[str, Query(max_length=39)] = "",
    limit: Limit = DEFAULT_LIMIT,
) -> RepoPage:
    """Repositories this connection can see, newest push first, or matching ``query``."""
    return await access.run(
        caller,
        lambda cred: access.gateway.find_repos(cred, query=query, owner=owner, limit=limit),
    )


@router.post("", response_model=Repo, status_code=status.HTTP_201_CREATED)
async def create_repo(body: NewRepo, caller: CallerDep, access: AccessDep) -> Repo:
    """A new repository, for the connected account or the organisation named as ``owner``."""
    return await access.run(
        caller,
        lambda cred: access.gateway.create_repo(
            cred,
            name=body.name,
            owner=body.owner,
            visibility=body.visibility,
            description=body.description,
        ),
    )


@router.get("/{owner}/{name}", response_model=Repo)
async def get_repo(owner: Owner, name: Name, caller: CallerDep, access: AccessDep) -> Repo:
    repo = f"{owner}/{name}"
    return await access.run(caller, lambda cred: access.gateway.repo(cred, repo), repo=repo)


@router.patch("/{owner}/{name}", response_model=Repo)
async def update_repo(
    *, owner: Owner, name: Name, body: RepoChanges, caller: CallerDep, access: AccessDep
) -> Repo:
    repo = f"{owner}/{name}"
    changes = body.model_dump(exclude_none=True)
    return await access.run(
        caller, lambda cred: access.gateway.update_repo(cred, repo, changes), repo=repo
    )


@router.delete("/{owner}/{name}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_repo(owner: Owner, name: Name, caller: CallerDep, access: AccessDep) -> Response:
    repo = f"{owner}/{name}"
    await access.run(caller, lambda cred: access.gateway.delete_repo(cred, repo), repo=repo)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
