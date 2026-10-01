# Jobs: subscriptions and the signals that end them

This is the sibling side of the family's jobs contract (`docs/jobs.md` and ADR-0015 in
LUCY-assistant). The hub opens a subscription here when a person says "tell me when CI is
green" (`repos.watch`); this service watches GitHub and, when the condition holds -- or
cannot, or the subscription expires -- POSTs **one signed signal** to the URL the hub gave.

## The routes

| Route | Body / answer |
| --- | --- |
| `POST /v1/subscriptions` | `{repo, kind, target, expires_in_seconds? \| expires_at?, signal: {url, secret}}` → `201 {id, state: "running", expires_at}` |
| `GET /v1/subscriptions/{id}` | `{id, state: running\|fired\|failed\|expired, kind, repo, target, expires_at, summary, facts, excerpt}` |
| `DELETE /v1/subscriptions/{id}` | `204`. Another account's id, or one already deleted, is `404` (the hub treats that as released). |

Kinds (the hub's `WATCH_KINDS`, verbatim) and their targets:

| `kind` | `target` | Fires when |
| --- | --- | --- |
| `checks_settled` | exactly one of `number` (a pull request) or `ref` (branch or sha) | there is at least one check and **every** check has `status: completed`. `facts.conclusion` is `success` when all concluded success/neutral/skipped, else `failure`; the excerpt lists the failing checks and steps. |
| `pull_merged` | `number` | the pull request is merged. Closed without merging ends it `failed`. |
| `review_submitted` | `number` | a review newer than those present at subscribe time arrives. |
| `run_completed` | `run` (a job id as the checks give it, or a workflow run id) | it has `status: completed`. |

An unknown kind is a 422 naming the four. Lifetimes are capped at
`GHAPI_SUBSCRIPTION_MAX_SECONDS` (a week); with neither `expires_in_seconds` nor `expires_at`
a subscription lives `GHAPI_SUBSCRIPTION_DEFAULT_SECONDS`. Every subscription expires on its
own, so one the hub forgot cannot run for ever. An account may hold
`GHAPI_SUBSCRIPTIONS_PER_ACCOUNT` open ones.

## Who looks, and under whose credential

The poller has no person's token once the request that opened a subscription has returned,
and this service never stores a credential. So a subscription is looked at in three places:

1. **At subscribe time**, under the credential resolved for that request. This validates the
   target (a pull request that does not exist is a 404 and nothing is kept), records the
   baseline `review_submitted` waits past, and fires at once if the condition already holds.
2. **In the background**, every `GHAPI_POLL_SECONDS`, under the credential **held in memory
   from subscribe time** -- until the earliest of keyring's expiry for it (less the refresh
   margin), the subscription's own expiry, and `GHAPI_CREDENTIAL_HOLD_SECONDS`. A GitHub 401
   drops the held credential; a rate limit pauses the poller until GitHub's reset; an outage
   is simply tried again next time.
3. **On every `GET /v1/subscriptions/{id}`** -- which is how the hub's sweep asks, every two
   minutes, under its standing grant for the person. The request's credential is resolved
   from keyring as for any route (with the one forced refresh on a 401), the subscription is
   looked at, and that credential is held again for the poller.

When the held credential has lapsed, or after a restart (which forgets every held
credential), the subscription **stays `running`** and the hub's sweep drives it to its end.
A sweep that cannot get a credential right now answers the stored state rather than an
error. Expiry needs no credential: the poller and every `GET` expire what is past its time.

The choice, against the alternatives:

- **Storing the credential with the subscription** would make the poller independent of the
  hub, at the cost of a GitHub token at rest in a second place, outliving the request and
  invisible to the person. Keyring is the vault; this service is not.
- **Asking keyring for a credential in the background** is impossible by design: keyring
  only answers for the person whose token is presented.
- **Not polling at all** (only the sweep) would work, but costs up to two minutes of latency
  on every watch, where the held credential gives `GHAPI_POLL_SECONDS` for the first hour.

## The signal

```
POST {signal.url}
Content-Type: application/json
X-Lucy-Signature: sha256=<hex HMAC-SHA256 of the raw body, keyed with signal.secret>

{"state": "fired", "summary": "CI on octo/hello#42 failed: 1 of 7 checks",
 "facts": {"conclusion": "failure", "checks": 7, "failed": 1}, "excerpt": "lint: failure (ruff)"}
```

It is sent by `lucy_signals.deliver` as a background task once the ending is saved: `204`
or `409` is delivered, a refused connection or a 5xx is retried three times over about two
minutes, any other 4xx is not retried. The outcome is recorded on the row. A signal that
never lands costs latency, not the ending: the hub's sweep reads the state with `GET`.

The signal says **that** something happened, never the result: the woken turn reads that
through the capability. `summary` is one line, `facts` at most twelve scalars, `excerpt` at
most 1,500 characters.

## Storage

One SQLite table (`jobs/store.py`). The secret is stored as given, because an HMAC key has
to be held to sign; it authorises ending one subscription at the hub and nothing else, and
it is never returned by a route or logged. Neither is the signal URL. Ended subscriptions are
purged `GHAPI_SUBSCRIPTION_RETENTION_SECONDS` after their lifetime.

Set `GHAPI_SIGNAL_URL_PREFIXES` in production so a caller cannot make this service POST to
an arbitrary address.
