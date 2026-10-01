"""Issues: list, open, close or reopen, and comment (on an issue or a pull request)."""

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
from github_api.api.schemas import CommentRequest, IssueChanges, NewIssue
from github_api.github.models import Commented, Issue, IssuePage

router = APIRouter(prefix="/v1/repos/{owner}/{name}/issues", tags=["issues"])

IssueState = Literal["open", "closed", "all"]


@router.get("", response_model=IssuePage)
async def list_issues(  # noqa: PLR0913 - where, who, and the two filters
    *,
    owner: Owner,
    name: Name,
    caller: CallerDep,
    access: AccessDep,
    state: Annotated[IssueState, Query()] = "open",
    limit: Limit = DEFAULT_LIMIT,
) -> IssuePage:
    repo = f"{owner}/{name}"
    return await access.run(
        caller,
        lambda cred: access.gateway.issues(cred, repo, state=state, limit=limit),
        repo=repo,
    )


@router.post("", response_model=Issue, status_code=status.HTTP_201_CREATED)
async def open_issue(
    *, owner: Owner, name: Name, body: NewIssue, caller: CallerDep, access: AccessDep
) -> Issue:
    repo = f"{owner}/{name}"
    return await access.run(
        caller,
        lambda cred: access.gateway.open_issue(
            cred, repo, title=body.title, body=body.body, labels=body.labels
        ),
        repo=repo,
    )


@router.patch("/{number}", response_model=Issue)
async def set_issue_state(  # noqa: PLR0913 - where, which, what, who
    *,
    owner: Owner,
    name: Name,
    number: Number,
    body: IssueChanges,
    caller: CallerDep,
    access: AccessDep,
) -> Issue:
    repo = f"{owner}/{name}"
    return await access.run(
        caller,
        lambda cred: access.gateway.set_issue_state(cred, repo, number, body.state),
        repo=repo,
    )


@router.post("/{number}/comments", response_model=Commented, status_code=status.HTTP_201_CREATED)
async def comment(  # noqa: PLR0913 - where, which, what, who
    *,
    owner: Owner,
    name: Name,
    number: Number,
    body: CommentRequest,
    caller: CallerDep,
    access: AccessDep,
) -> Commented:
    repo = f"{owner}/{name}"
    return await access.run(
        caller, lambda cred: access.gateway.comment(cred, repo, number, body.body), repo=repo
    )
