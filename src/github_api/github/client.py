"""The real :class:`~github_api.github.protocols.GitHubGateway`, over GitHub's REST and GraphQL.

Reads that would take several REST calls are one GraphQL query (see ``queries.py``); writes
are REST, because that is where GitHub documents them. Every answer goes through
``mappers.py`` -- no raw payload leaves this module.
"""

from __future__ import annotations

import base64
import re
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

from github_api.github import mappers, queries
from github_api.github.errors import GitHubNotFoundError, GitHubUnprocessableError
from github_api.github.http import RAW_MEDIA
from github_api.github.models import (
    Branch,
    Cancelled,
    ChangedFilePage,
    Checks,
    Commented,
    Committed,
    Dispatched,
    FileExcerpt,
    Identity,
    Issue,
    IssuePage,
    LatestReview,
    Merged,
    Pull,
    PullDetail,
    PullPage,
    Queued,
    Repo,
    RepoPage,
    Reviewed,
    RunStatus,
    Tree,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from github_api.github.http import GitHubHttp
    from github_api.github.models import GitHubCredential

PULL_STATES: dict[str, list[str] | None] = {
    "open": ["OPEN"],
    "closed": ["CLOSED", "MERGED"],
    "merged": ["MERGED"],
    "all": None,
}
ISSUE_STATES: dict[str, list[str] | None] = {"open": ["OPEN"], "closed": ["CLOSED"], "all": None}
REVIEW_EVENTS = {"approve": "APPROVE", "request_changes": "REQUEST_CHANGES", "comment": "COMMENT"}
PULL_REF = re.compile(r"^pull/(\d+)$")
LAST_PAGE = re.compile(r'[?&]page=(\d+)[^>]*>;\s*rel="last"')
COMMIT_SHA = re.compile(r"^[0-9a-f]{40}$")
FILE_MODE = "100644"


class GitHubClient:
    """GitHub, for one credential per call."""

    def __init__(
        self,
        http: GitHubHttp,
        *,
        file_chars_max: int,
        tree_entries_max: int,
        patch_chars_max: int,
    ) -> None:
        self._http = http
        self._file_chars_max = file_chars_max
        self._tree_entries_max = tree_entries_max
        self._patch_chars_max = patch_chars_max

    async def aclose(self) -> None:
        await self._http.aclose()

    async def ready(self) -> tuple[bool, str | None]:
        return await self._http.ready()

    async def visible(self, cred: GitHubCredential, repo: str) -> bool:
        try:
            await self._http.json("GET", _repo_path(repo), cred=cred)
        except GitHubNotFoundError:
            return False
        return True

    # ------------------------------------------------------------------ identity

    async def me(self, cred: GitHubCredential) -> Identity:
        user = await self._http.json("GET", "/user", cred=cred)
        login = mappers.text(user, "login")
        if cred.kind != "app":
            count = await self._count(
                cred, "/user/repos", {"affiliation": "owner,collaborator,organization_member"}
            )
            return Identity(login=login, kind="pat", selection="", repositories=count)
        installs = await self._http.json(
            "GET", "/user/installations", cred=cred, params={"per_page": 100}
        )
        selection, total = "", 0
        for install in mappers.rows(installs, "installations"):
            everything = mappers.text(install, "repository_selection") == "all"
            selection = "all" if everything or selection == "all" else "selected"
            page = await self._http.json(
                "GET",
                f"/user/installations/{mappers.number(install, 'id')}/repositories",
                cred=cred,
                params={"per_page": 1},
            )
            total += mappers.number(page, "total_count")
        return Identity(login=login, kind="app", selection=selection, repositories=total)

    async def _count(self, cred: GitHubCredential, path: str, params: Mapping[str, Any]) -> int:
        """An exact count from a REST list: one item per page, so the last page is the count."""
        response = await self._http.send("GET", path, cred=cred, params={**params, "per_page": 1})
        found = LAST_PAGE.search(response.headers.get("Link", ""))
        if found is not None:
            return int(found.group(1))
        body = response.json()
        return len(body) if isinstance(body, list) else 0

    # ------------------------------------------------------------------ repositories

    async def find_repos(
        self, cred: GitHubCredential, *, query: str, owner: str, limit: int
    ) -> RepoPage:
        if not query and not owner:
            found = await self._http.graphql(
                cred, queries.VIEWER_REPOS, {"first": limit}, root=("viewer", "repositories")
            )
            total = mappers.number(found, "totalCount")
        else:
            words = " ".join(part for part in (query, f"user:{owner}" if owner else "") if part)
            found = await self._http.graphql(
                cred, queries.SEARCH_REPOS, {"q": words, "first": limit}, root=("search",)
            )
            total = mappers.number(found, "repositoryCount")
        repos = [
            mappers.repo_node(node)
            for node in mappers.rows(found, "nodes")
            if mappers.text(node, "nameWithOwner")
        ]
        return RepoPage(repos=repos, total=max(total, len(repos)))

    async def repo(self, cred: GitHubCredential, repo: str) -> Repo:
        owner, name = _split(repo)
        node = await self._http.graphql(
            cred, queries.REPO, {"owner": owner, "name": name}, root=("repository",)
        )
        return mappers.repo_node(node)

    async def create_repo(
        self, cred: GitHubCredential, *, name: str, owner: str, visibility: str, description: str
    ) -> Repo:
        body: dict[str, Any] = {"name": name, "description": description}
        login = mappers.text(await self._http.json("GET", "/user", cred=cred), "login")
        if owner and owner.lower() != login.lower():
            path = f"/orgs/{quote(owner, safe='')}/repos"
            body["visibility"] = visibility
        else:
            if visibility == "internal":
                message = "`visibility: internal` exists only for an organisation's repository"
                raise GitHubUnprocessableError(message)
            path = "/user/repos"
            body["private"] = visibility != "public"
        return mappers.repo_rest(await self._http.json("POST", path, cred=cred, body=body))

    async def update_repo(
        self, cred: GitHubCredential, repo: str, changes: Mapping[str, Any]
    ) -> Repo:
        payload = await self._http.json("PATCH", _repo_path(repo), cred=cred, body=dict(changes))
        return await self.repo(cred, mappers.text(payload, "full_name") or repo)

    async def delete_repo(self, cred: GitHubCredential, repo: str) -> None:
        await self._http.json("DELETE", _repo_path(repo), cred=cred)

    # ------------------------------------------------------------------ pull requests

    async def pulls(self, cred: GitHubCredential, repo: str, *, state: str, limit: int) -> PullPage:
        owner, name = _split(repo)
        found = await self._http.graphql(
            cred,
            queries.PULLS,
            {"owner": owner, "name": name, "states": PULL_STATES[state], "first": limit},
            root=("repository", "pullRequests"),
        )
        pulls = [mappers.pull_node(node) for node in mappers.rows(found, "nodes")]
        return PullPage(pulls=pulls, total=max(mappers.number(found, "totalCount"), len(pulls)))

    async def pull(self, cred: GitHubCredential, repo: str, number: int) -> PullDetail:
        node = await self._pull_query(cred, repo, number, queries.PULL_DETAIL)
        return PullDetail(
            pull=mappers.pull_node(node),
            body=mappers.text(node, "body"),
            reviews=[mappers.review_node(row) for row in mappers.nodes(node, "reviews")],
            threads=[mappers.thread_node(row) for row in mappers.nodes(node, "reviewThreads")],
            checks=mappers.checks(mappers.last_commit_rollup(node, "latest")).checks,
        )

    async def changes(
        self, cred: GitHubCredential, repo: str, number: int, *, limit: int
    ) -> ChangedFilePage:
        path = f"{_repo_path(repo)}/pulls/{number}"
        pull = await self._http.json("GET", path, cred=cred)
        listed = await self._http.json(
            "GET", f"{path}/files", cred=cred, params={"per_page": limit}
        )
        files = [
            mappers.changed_file(row, self._patch_chars_max)
            for row in (listed if isinstance(listed, list) else [])
            if isinstance(row, dict)
        ]
        total = max(mappers.number(pull, "changed_files"), len(files))
        return ChangedFilePage(files=files, total=total)

    async def pull_summary(self, cred: GitHubCredential, repo: str, number: int) -> Pull:
        return mappers.pull_node(await self._pull_query(cred, repo, number, queries.PULL))

    async def latest_review(self, cred: GitHubCredential, repo: str, number: int) -> LatestReview:
        return mappers.latest_review(
            await self._pull_query(cred, repo, number, queries.LATEST_REVIEW)
        )

    async def _pull_query(self, cred: GitHubCredential, repo: str, number: int, query: str) -> Any:
        owner, name = _split(repo)
        return await self._http.graphql(
            cred,
            query,
            {"owner": owner, "name": name, "number": number},
            root=("repository", "pullRequest"),
        )

    async def open_pull(self, cred: GitHubCredential, repo: str, fields: Mapping[str, Any]) -> Pull:
        body = dict(fields)
        if not body.get("base"):
            body["base"] = await self._default_branch(cred, repo)
        payload = await self._http.json("POST", f"{_repo_path(repo)}/pulls", cred=cred, body=body)
        return await self.pull_summary(cred, repo, mappers.number(payload, "number"))

    async def update_pull(
        self, cred: GitHubCredential, repo: str, number: int, changes: Mapping[str, Any]
    ) -> Pull:
        path = f"{_repo_path(repo)}/pulls/{number}"
        plain = {key: changes[key] for key in ("title", "body", "state") if key in changes}
        node_id = ""
        if plain:
            node_id = mappers.text(
                await self._http.json("PATCH", path, cred=cred, body=plain), "node_id"
            )
        if "draft" in changes:
            if not node_id:
                node_id = mappers.text(await self._http.json("GET", path, cred=cred), "node_id")
            mutation = queries.TO_DRAFT if changes["draft"] else queries.READY_FOR_REVIEW
            await self._http.graphql(cred, mutation, {"id": node_id}, root=("done",))
        return await self.pull_summary(cred, repo, number)

    async def merge(
        self, cred: GitHubCredential, repo: str, number: int, *, method: str, delete_branch: bool
    ) -> Merged:
        path = f"{_repo_path(repo)}/pulls/{number}"
        payload = await self._http.json(
            "PUT", f"{path}/merge", cred=cred, body={"merge_method": method}
        )
        result = mappers.merged(payload)
        if delete_branch and result.merged:
            pull = await self._http.json("GET", path, cred=cred)
            head = mappers.nested(pull, "head")
            same_repo = mappers.text(mappers.nested(head, "repo"), "full_name") == repo
            if same_repo:
                await self._delete_ref(cred, repo, mappers.text(head, "ref"))
        return result

    async def _delete_ref(self, cred: GitHubCredential, repo: str, branch: str) -> None:
        """Delete a branch that may already be gone (repositories can auto-delete on merge)."""
        try:
            await self._http.json("DELETE", _ref_path(repo, "refs", branch), cred=cred)
        except GitHubNotFoundError:
            return

    async def review(
        self, cred: GitHubCredential, repo: str, number: int, *, event: str, body: str
    ) -> Reviewed:
        document: dict[str, Any] = {"event": REVIEW_EVENTS[event]}
        if body:
            document["body"] = body
        payload = await self._http.json(
            "POST", f"{_repo_path(repo)}/pulls/{number}/reviews", cred=cred, body=document
        )
        return mappers.reviewed(payload)

    async def comment(self, cred: GitHubCredential, repo: str, number: int, body: str) -> Commented:
        payload = await self._http.json(
            "POST",
            f"{_repo_path(repo)}/issues/{number}/comments",
            cred=cred,
            body={"body": body},
        )
        return Commented(url=mappers.text(payload, "html_url"))

    # ------------------------------------------------------------------ issues

    async def issues(
        self, cred: GitHubCredential, repo: str, *, state: str, limit: int
    ) -> IssuePage:
        owner, name = _split(repo)
        found = await self._http.graphql(
            cred,
            queries.ISSUES,
            {"owner": owner, "name": name, "states": ISSUE_STATES[state], "first": limit},
            root=("repository", "issues"),
        )
        issues = [mappers.issue_node(node) for node in mappers.rows(found, "nodes")]
        return IssuePage(issues=issues, total=max(mappers.number(found, "totalCount"), len(issues)))

    async def open_issue(
        self, cred: GitHubCredential, repo: str, *, title: str, body: str, labels: Sequence[str]
    ) -> Issue:
        payload = await self._http.json(
            "POST",
            f"{_repo_path(repo)}/issues",
            cred=cred,
            body={"title": title, "body": body, "labels": list(labels)},
        )
        return mappers.issue_rest(payload, repo)

    async def set_issue_state(
        self, cred: GitHubCredential, repo: str, number: int, state: str
    ) -> Issue:
        payload = await self._http.json(
            "PATCH", f"{_repo_path(repo)}/issues/{number}", cred=cred, body={"state": state}
        )
        return mappers.issue_rest(payload, repo)

    # ------------------------------------------------------------------ CI

    async def checks(self, cred: GitHubCredential, repo: str, ref: str) -> Checks:
        owner, name = _split(repo)
        pull = PULL_REF.match(ref)
        if pull is not None:
            node = await self._http.graphql(
                cred,
                queries.PULL_CHECKS,
                {"owner": owner, "name": name, "number": int(pull.group(1))},
                root=("repository", "pullRequest"),
            )
            return mappers.checks(mappers.last_commit_rollup(node))
        node = await self._http.graphql(
            cred,
            queries.REF_CHECKS,
            {"owner": owner, "name": name, "ref": ref},
            root=("repository", "object"),
        )
        return mappers.checks(mappers.nested(node, "statusCheckRollup"))

    async def raw_log(self, cred: GitHubCredential, repo: str, job_id: str) -> str:
        return await self._http.download(
            f"{_repo_path(repo)}/actions/jobs/{quote(job_id, safe='')}/logs", cred=cred
        )

    async def run_status(self, cred: GitHubCredential, repo: str, run_id: str) -> RunStatus:
        actions = f"{_repo_path(repo)}/actions"
        ident = quote(run_id, safe="")
        try:
            payload = await self._http.json("GET", f"{actions}/jobs/{ident}", cred=cred)
        except GitHubNotFoundError:
            payload = await self._http.json("GET", f"{actions}/runs/{ident}", cred=cred)
        return mappers.job_status(payload)

    async def _run_of(self, cred: GitHubCredential, repo: str, ident: str) -> tuple[str, bool]:
        """The workflow run behind a job id (as the checks give them) or a run id itself."""
        actions = f"{_repo_path(repo)}/actions"
        try:
            job = await self._http.json("GET", f"{actions}/jobs/{quote(ident, safe='')}", cred=cred)
        except GitHubNotFoundError:
            return ident, False
        return mappers.text(job, "run_id"), True

    async def rerun(
        self, cred: GitHubCredential, repo: str, run_id: str, *, failed_only: bool
    ) -> Queued:
        actions = f"{_repo_path(repo)}/actions"
        run, is_job = await self._run_of(cred, repo, run_id)
        if failed_only:
            path = f"{actions}/runs/{quote(run, safe='')}/rerun-failed-jobs"
        elif is_job:
            path = f"{actions}/jobs/{quote(run_id, safe='')}/rerun"
        else:
            path = f"{actions}/runs/{quote(run, safe='')}/rerun"
        await self._http.json("POST", path, cred=cred)
        return Queued(queued=True)

    async def cancel_run(self, cred: GitHubCredential, repo: str, run_id: str) -> Cancelled:
        run, _ = await self._run_of(cred, repo, run_id)
        await self._http.json(
            "POST", f"{_repo_path(repo)}/actions/runs/{quote(run, safe='')}/cancel", cred=cred
        )
        return Cancelled(cancelled=True)

    async def dispatch(
        self,
        cred: GitHubCredential,
        repo: str,
        workflow: str,
        *,
        ref: str,
        inputs: Mapping[str, str],
    ) -> Dispatched:
        await self._http.json(
            "POST",
            f"{_repo_path(repo)}/actions/workflows/{quote(workflow, safe='')}/dispatches",
            cred=cred,
            body={"ref": ref, "inputs": dict(inputs)},
        )
        return Dispatched(dispatched=True)

    # ------------------------------------------------------------------ contents

    async def read_file(
        self, cred: GitHubCredential, repo: str, path: str, *, ref: str
    ) -> FileExcerpt:
        where = f"{_repo_path(repo)}/contents/{quote(path.strip('/'), safe='/')}"
        params = {"ref": ref} if ref else None
        payload = await self._http.json("GET", where, cred=cred, params=params)
        if isinstance(payload, list) or mappers.text(payload, "type") != "file":
            message = f"`{path}` is not a file; list a directory with the tree"
            raise GitHubUnprocessableError(message)
        if mappers.text(payload, "encoding") == "base64":
            data = base64.b64decode(mappers.text(payload, "content"))
        else:  # over a megabyte: GitHub sends no content inline, only on request
            data = (
                await self._http.send("GET", where, cred=cred, params=params, accept=RAW_MEDIA)
            ).content
        return FileExcerpt(
            path=mappers.text(payload, "path"),
            ref=ref or await self._default_branch(cred, repo),
            binary=_binary(data),
            **_excerpt("" if _binary(data) else data.decode("utf-8"), self._file_chars_max),
        )

    async def tree(self, cred: GitHubCredential, repo: str, path: str, *, ref: str) -> Tree:
        payload = await self._http.json(
            "GET",
            f"{_repo_path(repo)}/git/trees/{quote(ref or 'HEAD', safe='')}",
            cred=cred,
            params={"recursive": "1"},
        )
        prefix = f"{path.strip('/')}/" if path.strip("/") else ""
        matched = [
            row
            for row in mappers.rows(payload, "tree")
            if mappers.text(row, "path").startswith(prefix)
        ]
        if prefix and not matched:
            message = f"no directory `{path}` in {repo}"
            raise GitHubNotFoundError(message)
        entries = [mappers.tree_entry(row) for row in matched[: self._tree_entries_max]]
        return Tree(entries=entries, total=len(matched))

    # ------------------------------------------------------------------ commits and branches

    async def commit(  # noqa: PLR0913 - one commit: where, on which branch, what, why, from what
        self,
        cred: GitHubCredential,
        repo: str,
        *,
        branch: str,
        message: str,
        files: Sequence[Mapping[str, str]],
        base: str,
    ) -> Committed:
        git = f"{_repo_path(repo)}/git"
        parent = await self._ref_sha(cred, repo, branch)
        created = parent is None
        if parent is None:
            parent = await self._start_sha(cred, repo, base)
        head = await self._http.json("GET", f"{git}/commits/{parent}", cred=cred)
        tree = await self._http.json(
            "POST",
            f"{git}/trees",
            cred=cred,
            body={
                "base_tree": mappers.text(mappers.nested(head, "tree"), "sha"),
                "tree": [
                    {"path": f["path"], "mode": FILE_MODE, "type": "blob", "content": f["content"]}
                    for f in files
                ],
            },
        )
        made = await self._http.json(
            "POST",
            f"{git}/commits",
            cred=cred,
            body={"message": message, "tree": mappers.text(tree, "sha"), "parents": [parent]},
        )
        sha = mappers.text(made, "sha")
        if created:
            await self._http.json(
                "POST", f"{git}/refs", cred=cred, body={"ref": f"refs/heads/{branch}", "sha": sha}
            )
        else:
            await self._http.json(
                "PATCH",
                _ref_path(repo, "refs", branch),
                cred=cred,
                body={"sha": sha, "force": False},
            )
        return Committed(sha=sha, branch=branch, url=mappers.text(made, "html_url"))

    async def create_branch(
        self, cred: GitHubCredential, repo: str, name: str, *, start: str
    ) -> Branch:
        sha = await self._start_sha(cred, repo, start)
        await self._http.json(
            "POST",
            f"{_repo_path(repo)}/git/refs",
            cred=cred,
            body={"ref": f"refs/heads/{name}", "sha": sha},
        )
        return Branch(name=name, sha=sha)

    async def delete_branch(self, cred: GitHubCredential, repo: str, name: str) -> None:
        await self._http.json("DELETE", _ref_path(repo, "refs", name), cred=cred)

    async def _start_sha(self, cred: GitHubCredential, repo: str, start: str) -> str:
        """The commit a new branch starts from: a sha as given, a branch, or the default."""
        if COMMIT_SHA.match(start):
            return start
        branch = start or await self._default_branch(cred, repo)
        sha = await self._ref_sha(cred, repo, branch)
        if sha is None:
            message = f"no branch `{branch}` in {repo} to start from"
            raise GitHubNotFoundError(message)
        return sha

    async def _ref_sha(self, cred: GitHubCredential, repo: str, branch: str) -> str | None:
        try:
            payload = await self._http.json("GET", _ref_path(repo, "ref", branch), cred=cred)
        except GitHubNotFoundError:
            return None
        return mappers.text(mappers.nested(payload, "object"), "sha")

    async def _default_branch(self, cred: GitHubCredential, repo: str) -> str:
        payload = await self._http.json("GET", _repo_path(repo), cred=cred)
        return mappers.text(payload, "default_branch")


def _split(repo: str) -> tuple[str, str]:
    owner, _, name = repo.partition("/")
    return owner, name


def _repo_path(repo: str) -> str:
    owner, name = _split(repo)
    return f"/repos/{quote(owner, safe='')}/{quote(name, safe='')}"


def _ref_path(repo: str, kind: str, branch: str) -> str:
    return f"{_repo_path(repo)}/git/{kind}/heads/{quote(branch, safe='/')}"


def _binary(data: bytes) -> bool:
    if b"\x00" in data:
        return True
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return True
    return False


def _excerpt(content: str, cap: int) -> dict[str, Any]:
    shown = content[:cap]
    return {
        "text": shown,
        "shown": len(shown),
        "total": len(content),
        "truncated": len(shown) < len(content),
    }


__all__ = ["GitHubClient"]
