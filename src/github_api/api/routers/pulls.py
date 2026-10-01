"""Pull requests: list, read, open, change, merge, review, and the files they change."""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Query, status

from github_api.api.dependencies import (
    DEFAULT_LIMIT,
    AccessDep,
    CallerDep,
    Limit,
    Name,
    Number,
    Owner,
)
from github_api.api.schemas import MergeRequest, NewPull, PullChanges, ReviewRequest
from github_api.github.models import ChangedFilePage, Merged, Pull, PullDetail, PullPage, Reviewed

router = APIRouter(prefix="/v1/repos/{owner}/{name}/pulls", tags=["pulls"])

PullState = Literal["open", "closed", "merged", "all"]


@router.get("", response_model=PullPage)
async def list_pulls(  # noqa: PLR0913 - where, who, and the two filters
    *,
    owner: Owner,
    name: Name,
    caller: CallerDep,
    access: AccessDep,
    state: Annotated[PullState, Query()] = "open",
    limit: Limit = DEFAULT_LIMIT,
) -> PullPage:
    repo = f"{owner}/{name}"
    return await access.run(
        caller,
        lambda cred: access.gateway.pulls(cred, repo, state=state, limit=limit),
        repo=repo,
    )


@router.post("", response_model=Pull, status_code=status.HTTP_201_CREATED)
async def open_pull(
    *, owner: Owner, name: Name, body: NewPull, caller: CallerDep, access: AccessDep
) -> Pull:
    repo = f"{owner}/{name}"
    fields = body.model_dump()
    return await access.run(
        caller, lambda cred: access.gateway.open_pull(cred, repo, fields), repo=repo
    )


@router.get("/{number}", response_model=PullDetail)
async def get_pull(
    *, owner: Owner, name: Name, number: Number, caller: CallerDep, access: AccessDep
) -> PullDetail:
    repo = f"{owner}/{name}"
    return await access.run(caller, lambda cred: access.gateway.pull(cred, repo, number), repo=repo)


@router.get("/{number}/files", response_model=ChangedFilePage)
async def list_changed_files(  # noqa: PLR0913 - where, which, who, how many
    *,
    owner: Owner,
    name: Name,
    number: Number,
    caller: CallerDep,
    access: AccessDep,
    limit: Limit = DEFAULT_LIMIT,
) -> ChangedFilePage:
    """The files a pull request changes, each patch cut to a bounded size."""
    repo = f"{owner}/{name}"
    return await access.run(
        caller, lambda cred: access.gateway.changes(cred, repo, number, limit=limit), repo=repo
    )


@router.patch("/{number}", response_model=Pull)
async def update_pull(  # noqa: PLR0913 - where, which, what, who
    *,
    owner: Owner,
    name: Name,
    number: Number,
    body: PullChanges,
    caller: CallerDep,
    access: AccessDep,
) -> Pull:
    repo = f"{owner}/{name}"
    changes = body.model_dump(exclude_none=True)
    return await access.run(
        caller, lambda cred: access.gateway.update_pull(cred, repo, number, changes), repo=repo
    )


@router.post("/{number}/merge", response_model=Merged)
async def merge_pull(  # noqa: PLR0913 - where, which, how, who
    *,
    owner: Owner,
    name: Name,
    number: Number,
    body: MergeRequest,
    caller: CallerDep,
    access: AccessDep,
) -> Merged:
    repo = f"{owner}/{name}"
    return await access.run(
        caller,
        lambda cred: access.gateway.merge(
            cred, repo, number, method=body.method, delete_branch=body.delete_branch
        ),
        repo=repo,
    )


@router.post("/{number}/reviews", response_model=Reviewed, status_code=status.HTTP_201_CREATED)
async def review_pull(  # noqa: PLR0913 - where, which, what, who
    *,
    owner: Owner,
    name: Name,
    number: Number,
    body: ReviewRequest,
    caller: CallerDep,
    access: AccessDep,
) -> Reviewed:
    repo = f"{owner}/{name}"
    return await access.run(
        caller,
        lambda cred: access.gateway.review(cred, repo, number, event=body.event, body=body.body),
        repo=repo,
    )
