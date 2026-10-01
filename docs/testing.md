# Testing

`make check` runs ruff (lint and format), mypy `--strict`, import-linter, and pytest with
100% branch coverage, warnings as errors and no `pragma: no cover`. Nothing touches the
network.

| Directory | What it pins | How |
| --- | --- | --- |
| `tests/unit/` | config rules, mappers (every one fed extra fields), the log window, the credential cache and keyring's errors, the access rules, the fake | plain calls; `FakeKeyring` for keyring |
| `tests/contract/` | GitHub's wire: statuses, rate-limit headers, GraphQL errors, redirects, and every `GitHubClient` method against GitHub's payloads | respx |
| `tests/integration/` | every route the hub calls, with the keys it reads; auth, audience, profile, 401 refresh, 403→404, 429 | ASGI app + `FakeKeyring` + `FakeGateway`; `test_real_client.py` uses the real client over respx |
| `tests/jobs/` | subscribe/sweep/release, the poller, conditions, expiry, signals signed and delivered | ASGI app or the service directly; signals land on an `httpx.MockTransport` and are checked with `lucy_signals.verify_signature` |

The seams are all on `create_app`: `transport` (GitHub), `keyring_transport`,
`signal_transport`, `gateway` (a whole fake GitHub), `clock` (keyring's `FakeClock`) and
`sleep` (so signal retries and the poller never wait).

Tokens are minted with `keyring_client.testing.mint` for audience `github-api`;
`FakeKeyring(service_tokens={"github-api": ...})` checks this service's token and the user
token's audience exactly as keyring does.
