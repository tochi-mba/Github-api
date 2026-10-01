# Github-api

The LUCY family's **repos** service: GitHub, reduced to what a person would say about it.
Lucy's hub calls it for the `repos` capability -- repositories, pull requests, issues, CI,
files and commits -- and for subscriptions that end with a signed signal when CI settles, a
pull request merges, a review lands or a job finishes.

It holds **no GitHub credential of its own**. Each request carries a keyring token minted
for audience `github-api` and names a keyring profile (`X-Keyring-Profile`); the service
resolves that person's GitHub credential from keyring for that profile, uses it for the call,
and caches it only until shortly before it expires. Everything it answers is a fixed, narrow
projection: no raw GitHub payload ever leaves it.

| | |
| --- | --- |
| Port | **8011** ([ADR-0016](https://github.com/tochi-mba/LUCY-assistant/blob/main/docs/adr/0016-repos-capability-and-port-8011.md) in the meta-repo) |
| Audience | `github-api` |
| Env prefix | `GHAPI_` (not `GITHUB_API_`: Actions runners export `GITHUB_API_URL`) |
| Auth pattern | credential consumer: verifies keyring JWTs, resolves `service="github"` from keyring |
| Contract | the hub's `src/lucy_api/clients/repos.py` -- every route and field it reads |
| Jobs | [docs/jobs.md](docs/jobs.md), the sibling side of the meta-repo's `docs/jobs.md` |

## Run locally

```bash
make install
cp .env.example .env          # set GHAPI_KEYRING_SERVICE_TOKEN
make check                    # ruff, mypy --strict, import contracts, pytest at 100% branch
make run                      # http://127.0.0.1:8011/docs
```

Keyring must list this service in `KEYRING_SERVICE_TOKENS` (as `github-api`) with the same
token as `GHAPI_KEYRING_SERVICE_TOKEN`, and the hub must be allowed to mint for audience
`github-api`. The tests need neither: they run against `keyring_client.testing.FakeKeyring`,
an in-memory GitHub (`github/fake.py`) and respx.

```bash
curl -sH "Authorization: Bearer $TOKEN" -H "X-Keyring-Profile: personal" \
  http://127.0.0.1:8011/v1/me
# {"login":"octo","kind":"app","selection":"selected","repositories":4}
```

## Registering the Lucy GitHub App (operator, once)

On GitHub: *Settings → Developer settings → GitHub Apps → New GitHub App*.

- [ ] **Callback URL**: keyring's `KEYRING_OAUTH_REDIRECT_URI` (e.g.
      `http://127.0.0.1:8001/v1/oauth/callback`).
- [ ] **Request user authorization (OAuth) during installation**: **on**.
- [ ] **Expire user authorization tokens**: **ON -- required.** Keyring refuses to renew a
      credential without a `refresh_token`; with expiry off GitHub sends none, and every
      connection breaks when keyring's assumed lifetime runs out. With it on, GitHub sends
      `expires_in` (eight hours) and a refresh token, and this service sees a fresh token
      after each renewal (it caches only until shortly before `expires_at`).
- [ ] **Webhook**: off. This service polls; see [docs/jobs.md](docs/jobs.md).
- [ ] **Repository permissions**:
  - Contents: **read and write** (files, trees, commits, branches)
  - Pull requests: **read and write** (list, open, update, merge, review)
  - Issues: **read and write** (list, open, close, comment)
  - Actions: **read and write** (job logs, re-run, cancel)
  - Checks: **read and write** (check runs and their steps)
  - Workflows: **read and write** (dispatch; commits that touch `.github/workflows`)
  - Administration: **read and write** (create, delete, change visibility)
  - Metadata: **read** (mandatory)
- [ ] **Where can it be installed**: any account. Installing it is where a person picks all
      repositories or only selected ones; `GET /v1/me` reports which (`selection`).
- [ ] Put the app's client id and secret in keyring's `providers.json` (mode 0600) as
      provider `github`, with `authorize_url` `https://github.com/login/oauth/authorize`
      and `token_url` `https://github.com/login/oauth/access_token`.

**Fine-grained personal access tokens work too**, for a person who wants to choose
repositories and permissions token by token: store one with keyring's api-key route,
`PUT /v1/profiles/{profile}/connections/github/api-key` (`{"api_key": "github_pat_..."}`).
`GET /v1/me` then reports `kind: "pat"`. A token cannot report which repositories it was
limited to, so `selection` is empty for one; `repositories` counts what it can reach.

## What is where

| Piece | Where |
| --- | --- |
| Routes (one module per resource) | `src/github_api/api/routers/` |
| Problem documents and codes | `src/github_api/api/errors.py` |
| Bearer verification, exact audience | `src/github_api/auth/verifier.py` |
| One operation under the caller's credential (refresh, 403→404) | `src/github_api/access.py` |
| Keyring credential resolution and cache | `src/github_api/credentials/keyring.py` |
| The GitHub seam, real and fake | `src/github_api/github/{protocols,client,fake}.py` |
| Rate limits, statuses, GraphQL errors | `src/github_api/github/http.py` |
| Raw payload → public model | `src/github_api/github/mappers.py`, `models.py` |
| Subscriptions, poller, signals | `src/github_api/jobs/` |

## Family code it depends on

Both come from a public tag, as a git source in `[tool.uv.sources]`: `lucy-signals` (the
signed-signal sender) from the LUCY-assistant tag `lucy-signals-v0.1.0`, and
`keyring-client` from the Keyring-api tag `keyring-client-v0.1.0`, as every sibling's does.

See [AGENTS.md](AGENTS.md) before changing anything, and [docs/](docs/) for the rest.
