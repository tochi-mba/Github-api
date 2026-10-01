"""CI: the checks on a ref, a window on one job's log, and re-running, cancelling, starting."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Path, Query, status

from github_api.api.dependencies import AccessDep, CallerDep, ContainerDep, Name, Owner
from github_api.api.schemas import DispatchRequest, RerunRequest
from github_api.github.logs import window
from github_api.github.models import Cancelled, Checks, Dispatched, LogExcerpt, Queued

router = APIRouter(prefix="/v1/repos/{owner}/{name}", tags=["checks"])

RunId = Annotated[str, Path(pattern=r"^\d{1,20}$", description="A job id, as the checks give it.")]
DEFAULT_LOG_LINES = 120


@router.get("/checks", response_model=Checks)
async def list_checks(
    *,
    owner: Owner,
    name: Name,
    caller: CallerDep,
    access: AccessDep,
    ref: Annotated[str, Query(min_length=1, description="`pull/<number>`, a branch or a sha.")],
) -> Checks:
    """Every check on the ref's head commit, and the combined state as one word."""
    repo = f"{owner}/{name}"
    return await access.run(caller, lambda cred: access.gateway.checks(cred, repo, ref), repo=repo)


@router.get("/checks/{run_id}/log", response_model=LogExcerpt)
async def read_log(  # noqa: PLR0913 - where, which job, who, and the window
    *,
    owner: Owner,
    name: Name,
    run_id: RunId,
    caller: CallerDep,
    container: ContainerDep,
    starting_at: Annotated[str, Query(alias="from", max_length=200)] = "",
    lines: Annotated[int, Query(ge=1)] = DEFAULT_LOG_LINES,
) -> LogExcerpt:
    """``lines`` lines from the first containing ``from``; without one, the last ``lines``."""
    repo = f"{owner}/{name}"
    access = container.access
    raw = await access.run(
        caller, lambda cred: access.gateway.raw_log(cred, repo, run_id), repo=repo
    )
    return window(raw, starting_at=starting_at, lines=min(lines, container.settings.log_lines_max))


@router.post("/checks/{run_id}/rerun", response_model=Queued, status_code=status.HTTP_202_ACCEPTED)
async def rerun(  # noqa: PLR0913 - where, which job, how, who
    *,
    owner: Owner,
    name: Name,
    run_id: RunId,
    body: RerunRequest,
    caller: CallerDep,
    access: AccessDep,
) -> Queued:
    repo = f"{owner}/{name}"
    return await access.run(
        caller,
        lambda cred: access.gateway.rerun(cred, repo, run_id, failed_only=body.failed_only),
        repo=repo,
    )


@router.post(
    "/runs/{run_id}/cancel", response_model=Cancelled, status_code=status.HTTP_202_ACCEPTED
)
async def cancel_run(
    *, owner: Owner, name: Name, run_id: RunId, caller: CallerDep, access: AccessDep
) -> Cancelled:
    """Cancel the workflow run a job belongs to (or the run, given a run id)."""
    repo = f"{owner}/{name}"
    return await access.run(
        caller, lambda cred: access.gateway.cancel_run(cred, repo, run_id), repo=repo
    )


@router.post(
    "/workflows/{workflow}/dispatch",
    response_model=Dispatched,
    status_code=status.HTTP_202_ACCEPTED,
)
async def dispatch(  # noqa: PLR0913 - where, which workflow, on what, who
    *,
    owner: Owner,
    name: Name,
    workflow: Annotated[str, Path(pattern=r"^[A-Za-z0-9._-]{1,200}$")],
    body: DispatchRequest,
    caller: CallerDep,
    access: AccessDep,
) -> Dispatched:
    """Start a workflow by its file name (``ci.yml``) or id, on a ref, with inputs."""
    repo = f"{owner}/{name}"
    return await access.run(
        caller,
        lambda cred: access.gateway.dispatch(
            cred, repo, workflow, ref=body.ref, inputs=body.inputs
        ),
        repo=repo,
    )
