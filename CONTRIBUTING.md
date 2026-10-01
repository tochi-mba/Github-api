# Contributing

Read [AGENTS.md](AGENTS.md) first -- it lists the invariants, and most of them are enforced
by a test or an import contract.

## Getting set up

```bash
make install
cp .env.example .env    # GHAPI_KEYRING_SERVICE_TOKEN is required
make check
```

Optionally install the git hooks in `.pre-commit-config.yaml` (`uv tool install pre-commit`,
then `pre-commit install`) -- a fast subset of `make check` on staged files.

## The loop

1. Write the test first, at the right level: a mapper or the log window in `tests/unit/`,
   GitHub's wire in `tests/contract/` (respx), a route in `tests/integration/` (ASGI, fake
   keyring, fake GitHub), the poller and signals in `tests/jobs/`.
2. Write the smallest implementation that passes.
3. `make check` before every commit. Never pipe it to `head` or `tail`: that masks the exit
   code.

## Changing the contract

The hub's `clients/repos.py` is the other half of every route. A new field is added to the
model here *and* read there; a renamed one breaks the hub. Keep `docs/api.md` in step.

## This service touches people's code

- **Add an isolation test** for anything per-account: another account gets a 404, with the
  same body as something that does not exist.
- **Nothing new may reach a log or a response** that could hold a credential.
- **A write names its repository explicitly.** Nothing here infers a target.

## Commits

Conventional-commit subject (`feat(scope):`, `fix(scope):`, `chore:`), imperative, no
trailing period. The body explains why. Write an ADR in [docs/adr/](docs/adr/) for any
decision future-you would otherwise re-litigate; family decisions stay in the meta-repo
([CONTRIBUTING.md](https://github.com/tochi-mba/LUCY-assistant/blob/main/CONTRIBUTING.md)).
