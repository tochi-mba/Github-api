"""Raw GitHub payloads in, fixed public models out.

Every function here *takes* named fields and nothing else, so a payload that grows cannot
grow what this service answers with. The readers are forgiving on purpose: a field GitHub
stops sending, or a nested object the token may not read (GraphQL answers those as null),
costs that one field and not the whole answer.
"""

from __future__ import annotations

from typing import Any

from github_api.github.models import (
    ChangedFile,
    CheckRun,
    Checks,
    Issue,
    LatestReview,
    Merged,
    Pull,
    Repo,
    Review,
    Reviewed,
    RunStatus,
    Thread,
    TreeEntry,
)

GHOST = "ghost"
"""GitHub's name for a deleted account, which is what a null author means."""

ROLLUP = {
    "SUCCESS": "success",
    "FAILURE": "failure",
    "ERROR": "failure",
    "PENDING": "pending",
    "EXPECTED": "pending",
}

MERGE_STATE = {
    "CLEAN": "clean",
    "HAS_HOOKS": "clean",
    "BLOCKED": "blocked",
    "DRAFT": "blocked",
    "DIRTY": "dirty",
    "BEHIND": "behind",
    "UNSTABLE": "unstable",
}

FAILED_STEP = frozenset({"FAILURE", "TIMED_OUT", "STARTUP_FAILURE"})

STATUS_CONTEXT = {
    "SUCCESS": ("completed", "success"),
    "FAILURE": ("completed", "failure"),
    "ERROR": ("completed", "failure"),
    "PENDING": ("in_progress", ""),
    "EXPECTED": ("queued", ""),
}

TREE_TYPES = {"blob": "file", "tree": "dir", "commit": "submodule"}


# ---------------------------------------------------------------------- readers


def text(payload: Any, key: str, default: str = "") -> str:
    value = payload.get(key) if isinstance(payload, dict) else None
    return default if value is None else str(value)


def number(payload: Any, key: str, default: int = 0) -> int:
    value = payload.get(key) if isinstance(payload, dict) else None
    if isinstance(value, bool):
        return default
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def flag(payload: Any, key: str) -> bool:
    value = payload.get(key) if isinstance(payload, dict) else None
    return value is True


def nested(payload: Any, *keys: str) -> Any:
    """``payload[k1][k2]...``, or an empty dict as soon as one is missing or not a dict."""
    found = payload
    for key in keys:
        found = found.get(key) if isinstance(found, dict) else None
    return found if isinstance(found, dict) else {}


def rows(payload: Any, key: str) -> list[Any]:
    value = payload.get(key) if isinstance(payload, dict) else None
    return [row for row in value if isinstance(row, dict)] if isinstance(value, list) else []


def nodes(payload: Any, *keys: str) -> list[Any]:
    """The ``nodes`` list of a GraphQL connection at ``keys``."""
    return rows(nested(payload, *keys), "nodes")


def login(payload: Any, key: str = "author") -> str:
    """Who, from a GraphQL actor or a REST user; a deleted account is ``ghost``."""
    return text(nested(payload, key), "login", GHOST)


def lower(value: str) -> str:
    return value.lower()


def rollup(state: Any) -> str:
    """A combined CI state in the four words the contract uses."""
    return ROLLUP.get(str(state), "none")


# ---------------------------------------------------------------------- repositories


def repo_node(node: Any) -> Repo:
    branch = nested(node, "defaultBranchRef")
    return Repo(
        full_name=text(node, "nameWithOwner"),
        private=flag(node, "isPrivate"),
        default_branch=text(branch, "name"),
        description=text(node, "description"),
        open_pulls=number(nested(node, "pullRequests"), "totalCount"),
        open_issues=number(nested(node, "issues"), "totalCount"),
        ci=rollup(nested(branch, "target", "statusCheckRollup").get("state")),
        url=text(node, "url"),
    )


def repo_rest(payload: Any) -> Repo:
    """A repository GitHub has just created: no pull requests and no CI yet."""
    return Repo(
        full_name=text(payload, "full_name"),
        private=flag(payload, "private"),
        default_branch=text(payload, "default_branch"),
        description=text(payload, "description"),
        open_pulls=0,
        open_issues=number(payload, "open_issues_count"),
        ci="none",
        url=text(payload, "html_url"),
    )


# ---------------------------------------------------------------------- pull requests


def pull_node(node: Any) -> Pull:
    [*_, last] = nodes(node, "commits") or [{}]
    return Pull(
        repo=text(nested(node, "repository"), "nameWithOwner"),
        number=number(node, "number"),
        title=text(node, "title"),
        state=lower(text(node, "state")),
        author=login(node),
        draft=flag(node, "isDraft"),
        head=text(node, "headRefName"),
        base=text(node, "baseRefName"),
        mergeable=MERGE_STATE.get(text(node, "mergeStateStatus"), "unknown"),
        checks=rollup(nested(last, "commit", "statusCheckRollup").get("state")),
        url=text(node, "url"),
    )


def changed_file(payload: Any, cap: int) -> ChangedFile:
    """One file of a pull request, its patch cut to ``cap`` characters.

    GitHub omits the patch of a binary or very large change; that is reported as truncated,
    because something was not shown. A rename that changed nothing has no patch to omit.
    """
    patch = text(payload, "patch")
    status = text(payload, "status")
    changed = number(payload, "additions") + number(payload, "deletions")
    omitted = not patch and (changed > 0 or status != "renamed")
    return ChangedFile(
        path=text(payload, "filename"),
        status=status,
        additions=number(payload, "additions"),
        deletions=number(payload, "deletions"),
        patch=patch[:cap],
        patch_truncated=len(patch) > cap or omitted,
        previous_path=text(payload, "previous_filename"),
    )


def review_node(node: Any) -> Review:
    return Review(author=login(node), state=lower(text(node, "state")), body=text(node, "body"))


def thread_node(node: Any) -> Thread:
    [first, *_] = nodes(node, "comments") or [{}]
    return Thread(
        path=text(node, "path"),
        line=number(node, "line") or number(node, "originalLine"),
        author=login(first) if first else "",
        body=text(first, "body"),
        resolved=flag(node, "isResolved"),
    )


def latest_review(node: Any) -> LatestReview:
    connection = nested(node, "reviews")
    [*_, last] = rows(connection, "nodes") or [None]
    return LatestReview(
        total=number(connection, "totalCount"),
        latest=review_node(last) if last is not None else None,
    )


def reviewed(payload: Any) -> Reviewed:
    return Reviewed(state=lower(text(payload, "state")), url=text(payload, "html_url"))


def merged(payload: Any) -> Merged:
    return Merged(
        merged=flag(payload, "merged"), sha=text(payload, "sha"), message=text(payload, "message")
    )


# ---------------------------------------------------------------------- checks


def check_context(node: Any) -> CheckRun | None:
    """One check from a rollup: an Actions/App check run, or a commit status."""
    kind = text(node, "__typename")
    if kind == "CheckRun":
        return CheckRun(
            id=text(node, "databaseId"),
            name=text(node, "name"),
            status=lower(text(node, "status")),
            conclusion=lower(text(node, "conclusion")),
            workflow=text(nested(node, "checkSuite", "workflowRun", "workflow"), "name"),
            failing_steps=[
                text(step, "name")
                for step in nodes(node, "steps")
                if text(step, "conclusion") in FAILED_STEP
            ],
            url=text(node, "detailsUrl"),
        )
    if kind == "StatusContext":
        status, conclusion = STATUS_CONTEXT.get(text(node, "state"), ("queued", ""))
        return CheckRun(
            id="",
            name=text(node, "context"),
            status=status,
            conclusion=conclusion,
            url=text(node, "targetUrl"),
        )
    return None


def checks(rollup_node: Any) -> Checks:
    """A rollup, or its absence, as a summary and the checks under it."""
    found = [check_context(node) for node in nodes(rollup_node, "contexts")]
    return Checks(
        summary=rollup(rollup_node.get("state") if isinstance(rollup_node, dict) else None),
        checks=[check for check in found if check is not None],
    )


def last_commit_rollup(node: Any, connection: str = "commits") -> Any:
    [*_, last] = nodes(node, connection) or [{}]
    return nested(last, "commit", "statusCheckRollup")


def job_status(payload: Any) -> RunStatus:
    return RunStatus(
        id=text(payload, "id"),
        name=text(payload, "name"),
        status=text(payload, "status"),
        conclusion=text(payload, "conclusion"),
        url=text(payload, "html_url"),
    )


# ---------------------------------------------------------------------- issues and files


def issue_node(node: Any) -> Issue:
    return Issue(
        repo=text(nested(node, "repository"), "nameWithOwner"),
        number=number(node, "number"),
        title=text(node, "title"),
        state=lower(text(node, "state")),
        author=login(node),
        labels=[text(label, "name") for label in nodes(node, "labels")],
        url=text(node, "url"),
    )


def issue_rest(payload: Any, repo: str) -> Issue:
    return Issue(
        repo=repo,
        number=number(payload, "number"),
        title=text(payload, "title"),
        state=text(payload, "state"),
        author=login(payload, "user"),
        labels=[text(label, "name") for label in rows(payload, "labels")],
        url=text(payload, "html_url"),
    )


def tree_entry(payload: Any) -> TreeEntry:
    kind = text(payload, "type")
    return TreeEntry(
        path=text(payload, "path"),
        type=TREE_TYPES.get(kind, kind),
        size=number(payload, "size"),
    )


__all__ = [
    "changed_file",
    "check_context",
    "checks",
    "issue_node",
    "issue_rest",
    "job_status",
    "last_commit_rollup",
    "latest_review",
    "merged",
    "pull_node",
    "repo_node",
    "repo_rest",
    "review_node",
    "reviewed",
    "rollup",
    "thread_node",
    "tree_entry",
]
