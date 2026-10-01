# API

Every `/v1` route needs `Authorization: Bearer <keyring token>` minted for audience
`github-api`, and reads `X-Keyring-Profile` (default `default`) to choose which of the
person's GitHub connections to use. The shapes are the hub's contract
(`src/lucy_api/clients/repos.py` in LUCY-assistant); OpenAPI is at `/docs` when running.

`{repo}` below is `{owner}/{name}`. Lists take `limit` (1..100, default 20) and answer with
`total`, so a cap is always confessed.

## Routes

| Method | Path | Body / query | Answer |
| --- | --- | --- | --- |
| GET | `/healthy` | | `{status: "alive", version, environment, uptime_seconds}` |
| GET | `/ready` | | `{status, checks: {keyring, github, database}}`; 503 when any is degraded |
| GET | `/v1/me` | | `{login, kind: app\|pat, selection: all\|selected\|"", repositories}` |
| GET | `/v1/repos` | `?query&owner&limit` | `{repos: [Repo], total}` |
| POST | `/v1/repos` | `{name, owner?, visibility?: private\|public\|internal, description?}` | `201 Repo` |
| GET | `/v1/repos/{repo}` | | `Repo` |
| PATCH | `/v1/repos/{repo}` | `{visibility?, description?, default_branch?, archived?}` | `Repo` |
| DELETE | `/v1/repos/{repo}` | | `204` |
| GET | `/v1/repos/{repo}/pulls` | `?state=open\|closed\|merged\|all&limit` | `{pulls: [Pull], total}` |
| POST | `/v1/repos/{repo}/pulls` | `{title, head, base?, body?, draft?}` | `201 Pull` |
| GET | `/v1/repos/{repo}/pulls/{number}` | | `{pull: Pull, body, reviews: [{author, state, body}], threads: [{path, line, author, body, resolved}], checks: [Check]}` |
| GET | `/v1/repos/{repo}/pulls/{number}/files` | `?limit` | `{files: [{path, status, additions, deletions, patch, patch_truncated, previous_path}], total}` |
| PATCH | `/v1/repos/{repo}/pulls/{number}` | `{title?, body?, draft?, state?: open\|closed}` | `Pull` |
| POST | `/v1/repos/{repo}/pulls/{number}/merge` | `{method?: merge\|squash\|rebase, delete_branch?}` | `{merged, sha, message}` |
| POST | `/v1/repos/{repo}/pulls/{number}/reviews` | `{event: approve\|request_changes\|comment, body}` | `201 {state, url}` |
| GET | `/v1/repos/{repo}/issues` | `?state=open\|closed\|all&limit` | `{issues: [Issue], total}` |
| POST | `/v1/repos/{repo}/issues` | `{title, body, labels}` | `201 Issue` |
| PATCH | `/v1/repos/{repo}/issues/{number}` | `{state: open\|closed}` | `Issue` |
| POST | `/v1/repos/{repo}/issues/{number}/comments` | `{body}` | `201 {url}` (pull requests too) |
| GET | `/v1/repos/{repo}/checks` | `?ref=pull/<n>\|<branch>\|<sha>` | `{summary: success\|failure\|pending\|none, checks: [Check]}` |
| GET | `/v1/repos/{repo}/checks/{id}/log` | `?from&lines` | `{text, shown, total, truncated}` |
| POST | `/v1/repos/{repo}/checks/{id}/rerun` | `{failed_only}` | `202 {queued}` |
| POST | `/v1/repos/{repo}/runs/{id}/cancel` | | `202 {cancelled}` |
| POST | `/v1/repos/{repo}/workflows/{workflow}/dispatch` | `{ref, inputs}` | `202 {dispatched}` |
| GET | `/v1/repos/{repo}/contents` | `?path&ref` | `{path, ref, text, shown, total, truncated, binary}` |
| GET | `/v1/repos/{repo}/tree` | `?path&ref` | `{entries: [{path, type: file\|dir\|submodule, size}], total}` |
| POST | `/v1/repos/{repo}/commits` | `{branch, message, files: [{path, content}], base?}` | `201 {sha, branch, url}` |
| POST | `/v1/repos/{repo}/branches` | `{name, start?}` | `201 {name, sha}` |
| DELETE | `/v1/repos/{repo}/branches/{name}` | (`name` may contain `/`, sent as `%2F`) | `204` |
| POST | `/v1/subscriptions` | `{repo, kind, target, expires_in_seconds? \| expires_at?, signal: {url, secret}}` | `201 {id, state, expires_at}` |
| GET | `/v1/subscriptions/{id}` | | `{id, state, kind, repo, target, expires_at, summary, facts, excerpt}` |
| DELETE | `/v1/subscriptions/{id}` | | `204`; `404` when not this account's or already gone |

The models:

- **Repo** `{full_name, private, default_branch, description, open_pulls, open_issues, ci, url}`
  -- `ci` is the default branch's combined check state.
- **Pull** `{repo, number, title, state: open|closed|merged, author, draft, head, base,
  mergeable: clean|blocked|dirty|behind|unstable|unknown, checks, url}`.
- **Check** `{id, name, status, conclusion, workflow, failing_steps, url}` -- `id` is the
  Actions job id the log, re-run and cancel routes take; a commit status has `id: ""`.
- **Issue** `{repo, number, title, state, author, labels: [str], url}`.

## Notes on particular routes

- **Log window.** `lines` lines from the first line containing `from`; without `from`, or
  when nothing matches, the last `lines` lines. GitHub's per-line timestamps are removed.
  `lines` is capped at `GHAPI_LOG_LINES_MAX`; `shown`/`total`/`truncated` say what was cut.
- **Files.** Text is capped at `GHAPI_FILE_CHARS_MAX` characters; a binary file answers
  `binary: true` with no text. A directory is a 422 pointing at the tree.
- **Changed files.** Each patch is cut to `GHAPI_PATCH_CHARS_MAX` characters (6,000);
  GitHub omits the patch of a binary or very large change, which is `patch: ""` with
  `patch_truncated: true`. `total` is the pull request's changed-file count.
- **Commits** go through the Git Data API: one tree, one commit, one ref update (never
  forced). A branch that does not exist is created from `base`, or from the default branch.
  A branch that moved meanwhile is a 409.
- **Re-run** takes a job id: `failed_only` re-runs the failed jobs of the job's workflow run,
  otherwise the job itself. A workflow run id is accepted as well.
- **Subscriptions** are described in [jobs.md](jobs.md).

## Errors

Every failure is `application/problem+json`:
`{type, title, status, detail, code}`. The `detail` names the fix.

| Status | `code` | Means |
| --- | --- | --- |
| 401 | `unauthenticated` | No token, or one for another audience, expired, or forged. |
| 403 | `forbidden` | The repository is visible, but the connection may not do this. |
| 404 | `not-found` | No such thing **that this connection can see** -- a hidden repository and a missing one answer identically. |
| 409 | `conflict` | Not mergeable, already exists, a branch that moved. |
| 409 | `too-many-subscriptions` | The account's open-subscription cap is full. |
| 422 | `invalid-request` | The request does not validate; the detail names each field. |
| 422 | `unprocessable` | GitHub refused it as sent; its reasons are in the detail. |
| 422 | `unknown-kind` / `invalid-subscription` | A subscription that cannot be opened as asked. |
| 429 | `rate-limited` | GitHub's rate limit; `Retry-After` carries GitHub's own interval. |
| 502 | `credential-missing` | GitHub is not connected on this profile. |
| 502 | `credential-unavailable` | Keyring holds a connection it cannot make usable, or GitHub refused it even after a refresh. |
| 502 | `keyring-rejected` | Keyring refused this service's own token (operator). |
| 502 | `github-unavailable` | GitHub is down, slow, or answered something unreadable. |
| 503 | `keyring-unavailable` | Keyring (or its signing keys) could not be reached. |
