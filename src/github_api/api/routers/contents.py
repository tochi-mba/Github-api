"""Files and branches: read a file, list a tree, commit files, make and delete branches."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Path, Query, Response, status

from github_api.api.dependencies import AccessDep, CallerDep, Name, Owner
from github_api.api.schemas import CommitRequest, NewBranch
from github_api.github.models import Branch, Committed, FileExcerpt, Tree

router = APIRouter(prefix="/v1/repos/{owner}/{name}", tags=["contents"])

Ref = Annotated[str, Query(max_length=255, description="A branch, tag or sha; default branch.")]


@router.get("/contents", response_model=FileExcerpt)
async def read_file(  # noqa: PLR0913 - where, which file, at what, who
    *,
    owner: Owner,
    name: Name,
    caller: CallerDep,
    access: AccessDep,
    path: Annotated[str, Query(min_length=1, max_length=1024)],
    ref: Ref = "",
) -> FileExcerpt:
    """One file's text, capped, with exactly how much of it is shown."""
    repo = f"{owner}/{name}"
    return await access.run(
        caller, lambda cred: access.gateway.read_file(cred, repo, path, ref=ref), repo=repo
    )


@router.get("/tree", response_model=Tree)
async def list_tree(  # noqa: PLR0913 - where, which directory, at what, who
    *,
    owner: Owner,
    name: Name,
    caller: CallerDep,
    access: AccessDep,
    path: Annotated[str, Query(max_length=1024)] = "",
    ref: Ref = "",
) -> Tree:
    """Every file and directory under ``path``, capped, with the total."""
    repo = f"{owner}/{name}"
    return await access.run(
        caller, lambda cred: access.gateway.tree(cred, repo, path, ref=ref), repo=repo
    )


@router.post("/commits", response_model=Committed, status_code=status.HTTP_201_CREATED)
async def commit(
    *, owner: Owner, name: Name, body: CommitRequest, caller: CallerDep, access: AccessDep
) -> Committed:
    """Write files to ``branch`` in one commit, creating it from ``base`` when it is new."""
    repo = f"{owner}/{name}"
    files = [{"path": one.path, "content": one.content} for one in body.files]
    return await access.run(
        caller,
        lambda cred: access.gateway.commit(
            cred, repo, branch=body.branch, message=body.message, files=files, base=body.base
        ),
        repo=repo,
    )


@router.post("/branches", response_model=Branch, status_code=status.HTTP_201_CREATED)
async def create_branch(
    *, owner: Owner, name: Name, body: NewBranch, caller: CallerDep, access: AccessDep
) -> Branch:
    repo = f"{owner}/{name}"
    return await access.run(
        caller,
        lambda cred: access.gateway.create_branch(cred, repo, body.name, start=body.start),
        repo=repo,
    )


@router.delete("/branches/{branch:path}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_branch(
    *,
    owner: Owner,
    name: Name,
    branch: Annotated[str, Path(min_length=1)],
    caller: CallerDep,
    access: AccessDep,
) -> Response:
    repo = f"{owner}/{name}"
    await access.run(
        caller, lambda cred: access.gateway.delete_branch(cred, repo, branch), repo=repo
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
