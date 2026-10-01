# Operations

Listen on `GHAPI_HOST`:`GHAPI_PORT` (default `127.0.0.1:8011`). Point container healthchecks
at `/healthy` and load balancers at `/ready`, which answers 503 when keyring's signing keys,
GitHub's unauthenticated `/rate_limit`, or the database cannot be reached.

Every variable is prefixed `GHAPI_`. An unknown `GHAPI_*` variable is a startup error, so a
misspelt setting fails loudly instead of silently not applying. (`GITHUB_API_URL`, which
Actions runners export, is not ours and is ignored.)

## Environment

| Variable | Default | Purpose |
| --- | --- | --- |
| `GHAPI_APP_NAME` | `github-api` | Name in logs. |
| `GHAPI_ENVIRONMENT` | `local` | Reported by the probes. |
| `GHAPI_LOG_LEVEL` | `INFO` | |
| `GHAPI_LOG_FORMAT` | `json` | `json` or `console`. |
| `GHAPI_HOST` / `GHAPI_PORT` | `127.0.0.1` / `8011` | Where to listen. |
| `GHAPI_KEYRING_JWKS_URL` | `http://127.0.0.1:8001/.well-known/jwks.json` | Keyring's signing keys. |
| `GHAPI_KEYRING_ISSUER` | `http://127.0.0.1:8001` | The `iss` every token must carry. |
| `GHAPI_AUDIENCE` | `github-api` | The exact `aud` every token must carry. No dots. |
| `GHAPI_JWKS_CACHE_SECONDS` | `3600` | How long keys are cached. |
| `GHAPI_JWKS_MIN_REFETCH_SECONDS` | `30` | Floor between refetches on an unknown key id. |
| `GHAPI_KEYRING_TIMEOUT_SECONDS` | `5` | Keyring calls. |
| `GHAPI_KEYRING_BASE_URL` | `http://127.0.0.1:8001` | Keyring's internal surface. |
| `GHAPI_KEYRING_SERVICE_TOKEN` | **required** | This service's token in keyring's `KEYRING_SERVICE_TOKENS` (`github-api`); 32+ characters. |
| `GHAPI_KEYRING_CREDENTIAL_SERVICE` | `github` | The connection name keyring stores GitHub under. |
| `GHAPI_CREDENTIAL_REFRESH_MARGIN_SECONDS` | `60` | Stop using a cached credential this long before it expires. |
| `GHAPI_CREDENTIAL_CACHE_SECONDS` | `300` | Cache lifetime when keyring gives no expiry. |
| `GHAPI_CREDENTIAL_CACHE_ENTRIES` | `1024` | Bound on cached credentials. |
| `GHAPI_GITHUB_BASE_URL` | `https://api.github.com` | REST. For GHES, `https://<host>/api/v3`. |
| `GHAPI_GITHUB_GRAPHQL_URL` | `https://api.github.com/graphql` | GraphQL. For GHES, `https://<host>/api/graphql`. |
| `GHAPI_REQUEST_TIMEOUT_SECONDS` | `15` | Every GitHub call. |
| `GHAPI_LOG_LINES_MAX` | `500` | Cap on a log window's `lines`. |
| `GHAPI_FILE_CHARS_MAX` | `100000` | Cap on a file read. |
| `GHAPI_TREE_ENTRIES_MAX` | `1000` | Cap on a tree listing. |
| `GHAPI_PATCH_CHARS_MAX` | `6000` | Cap on each changed file's patch. |
| `GHAPI_DATABASE_PATH` | `var/github-api.sqlite3` | Subscriptions. Its directory is created. |
| `GHAPI_POLL_SECONDS` | `60` | Background poll interval; `0` turns the poller off (the hub's sweep still drives subscriptions). |
| `GHAPI_SUBSCRIPTION_DEFAULT_SECONDS` | `3600` | Lifetime when the request names none. |
| `GHAPI_SUBSCRIPTION_MAX_SECONDS` | `604800` | Cap on any lifetime (a week). |
| `GHAPI_SUBSCRIPTIONS_PER_ACCOUNT` | `50` | Open subscriptions per account. |
| `GHAPI_SUBSCRIPTION_RETENTION_SECONDS` | `604800` | How long an ended subscription stays readable after its lifetime. |
| `GHAPI_CREDENTIAL_HOLD_SECONDS` | `3600` | Longest the poller holds a credential from a request. |
| `GHAPI_SIGNAL_URL_PREFIXES` | `[]` | JSON list; when set, signal URLs must start with one. Set it in production (e.g. `["http://lucy:8000/v1/signals/"]`). |
| `GHAPI_SIGNAL_TIMEOUT_SECONDS` | `10` | Each signal POST. |

## Keyring side

- `KEYRING_SERVICE_TOKENS` includes `"github-api": "<GHAPI_KEYRING_SERVICE_TOKEN>"`.
- The hub's exchange allowlist (`KEYRING_EXCHANGE_AUDIENCES` for `lucy-api`) includes
  `github-api`.
- An OAuth provider `github` (see the README's app checklist), or people store fine-grained
  tokens through the api-key route.

## Rate limits

GitHub's limits are per credential, so they are per person. When GitHub reports a bucket
empty (`X-RateLimit-Remaining: 0`), further calls with that credential to that bucket are
answered 429 locally with `Retry-After` until `X-RateLimit-Reset`. A secondary limit
(403/429 with `Retry-After`) is passed on the same way. The poller pauses entirely until the
reset it was given.

## Running in a container

`make docker` builds `github-api:local`. The image runs as uid 10001, keeps the database in
`/app/var` (a volume), and its healthcheck calls `/healthy`.
