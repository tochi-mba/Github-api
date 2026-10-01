# AGENTS.md

Working notes for Github-api. Read this before editing.

## What this service is

**github-api** is the LUCY family's `repos` service. The hub (`lucy-api` in
LUCY-assistant) calls it with a keyring token minted for audience `github-api` and a
keyring profile in `X-Keyring-Profile`; this service resolves the caller's GitHub credential
from keyring (`service="github"`) and answers in fixed, narrow models. It also runs
subscriptions -- durable watches that end with one signed signal to the hub.

**The contract is the hub's client**, `src/lucy_api/clients/repos.py` in LUCY-assistant:
every path, method, query parameter, body and response field. Change a route or a field
name here only together with that file.

## Commands

| Command | What it does |
| --- | --- |
| `make install` | Create the venv and install everything. |
| `make check` | Lint, types, import contracts, tests at 100% branch coverage. |
| `make run` | Serve on :8011 with reload. |
| `make test` | Tests only. |

## Invariants

- **No raw GitHub payload leaves `github/`.** Every answer is a model in
  `github/models.py`, built by a mapper that takes named fields only. A test feeds every
  mapper payloads with extra fields.
- **The credential is the caller's, per request.** Never a service-wide token. The cache key
  is `profile:sha256(user token)`, never the token. A credential never reaches a log, a
  response, an error message or the database; `GitHubCredential` and `Caller` redact
  themselves in `repr`.
- **One forced refresh on a GitHub 401**, then `502 credential-unavailable`. Missing
  connection: `502 credential-missing`. These two codes are what the hub reads as "not
  connected" -- do not invent a third spelling.
- **A repository the credential cannot see is a 404, never a 403**, with the same body as a
  repository that does not exist (`github/errors.NOT_FOUND`). A 403 is only reported for a
  repository the credential can see.
- **GitHub rate limits are 429 with `Retry-After`**, and a spent bucket is refused locally
  until GitHub's reset rather than asked again.
- **Errors are RFC 9457 problem documents with a `code`** (`api/errors.py`), and the
  `detail` names the fix.
- **Subscriptions never store a credential.** The signal secret is stored (it must be, to
  sign) and is never returned or logged. Another account's subscription is a 404.
- `/healthy` does no I/O and never fails. `/ready` checks keyring's keys, GitHub's
  unauthenticated `/rate_limit`, and the database.
- No `pragma: no cover`. No setting that disables verification. No file over 1000 lines.
- Routers never import `httpx`, `keyring_client`, `jwt`, `lucy_signals` or `sqlite3`; the
  layering is in `[tool.importlinter]` and is the architecture statement.

## Testing shape

`github/protocols.GitHubGateway` is the seam. `github/fake.FakeGateway` satisfies it for the
route and job tests; `github/client.GitHubClient` is tested against GitHub's payloads served
by respx (`tests/contract/`). `create_app(..., transport=, keyring_transport=,
signal_transport=, gateway=, clock=, sleep=)` keeps the whole suite offline.
