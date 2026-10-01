# Architecture

One process. Configuration loads from `GHAPI_*` variables; the lifespan builds the object
graph (`container.py`), starts the subscription poller, and closes everything on the way out.

```
HTTP ─► api.routers ─► api.dependencies (Bearer → TokenVerifier, X-Keyring-Profile → Caller)
             │
             ▼
         access.GitHubAccess.run(caller, operation, repo=...)
             │   resolve the caller's credential ─► credentials.keyring ─► keyring /v1/internal
             │   401 → one forced refresh; 403 on a hidden repository → 404
             ▼
         github.protocols.GitHubGateway
             ├── github.client.GitHubClient ─► github.http.GitHubHttp ─► api.github.com
             │        └── github.mappers → github.models (the only shapes that leave)
             └── github.fake.FakeGateway (tests)

         jobs.service.Subscriptions ─► jobs.store (SQLite)
             ├── jobs.conditions.evaluate(gateway, credential, subscription)
             └── jobs.delivery.SignalSender ─► lucy_signals.deliver ─► hub /v1/signals/{id}
```

## Layers

The import contracts in `pyproject.toml` are the architecture statement:

1. `api` -- routes, request bodies, problem documents. Never imports `httpx`,
   `keyring_client`, `jwt`, `lucy_signals` or `sqlite3` directly.
2. `container` -- wiring only.
3. `jobs` -- subscriptions: models, the SQLite store, conditions, the poller, delivery.
4. `access` -- one GitHub operation under one caller's credential.
5. `credentials` -- keyring's internal surface and the per-profile cache.
6. `auth` | `github` -- token verification; everything that speaks GitHub. `github` knows
   nothing of keyring, signals or FastAPI.
7. `core` -- configuration.

## Reads are GraphQL where it saves calls

A repository with its open counts and default-branch CI, a pull request with its reviews,
review threads (whose resolution REST cannot report) and checks with their failing steps,
and the checks on a ref -- each is one GraphQL query (`github/queries.py`). Writes are REST,
where GitHub documents them. Both pass through `GitHubHttp`, which owns the rate-limit
buckets (`core` and `graphql`, per credential) and the mapping from GitHub's answers to the
error vocabulary.

## Credentials

Keyring will not let a service name a person: `resolve_credential(user_token, profile,
service="github")` returns headers for whoever the token belongs to. `KeyringCredentials`
caches the answer per `profile:sha256(token)` until `expires_at` less a margin (or a short
default when keyring gives no expiry), bounded in size. A GitHub 401 gets one forced
re-resolve -- keyring renews an expiring GitHub user token on resolve -- and a second 401 is
reported as `credential-unavailable` with the cache entry dropped.

`kind` (`app` or `pat`) is read from the token's prefix (`ghu_`/`gho_` are GitHub App user
and OAuth tokens); nothing else about the credential is ever inspected.

## Storage

SQLite (`GHAPI_DATABASE_PATH`) holds subscriptions only. No credential is stored; the
signal secret is, because an HMAC key has to be held to sign with. See [jobs.md](jobs.md).
