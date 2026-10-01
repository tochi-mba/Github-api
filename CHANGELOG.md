# Changelog

All notable changes to this service follow [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added

- A GitHub Pages site at <https://tochi-mba.github.io/Github-api/>, in the REX ink/signal
  style: what Github-api is, its API, how to run it and what it will not do. `site/` is plain
  static HTML;
  `.github/workflows/pages.yml` publishes it after `scripts/check_site.py` has checked every
  page for a broken anchor, a missing asset, an image without alt text or draft text.
- The repository is attributed to REX Technologies: the LICENSE copyright holder, the package
  author and the README.
- The `repos` contract the hub's `clients/repos.py` speaks: identity (`/v1/me`), repositories
  (find, read, create, change, delete), pull requests (list, read with reviews, threads and
  checks, changed files, open, update, merge, review), issues (list, open, close, comment),
  CI (checks on a ref, a log window, re-run, cancel, dispatch), files (read, tree), commits
  through the Git Data API (optionally on a new branch from a base), and branches.
- The caller's GitHub credential resolved from keyring per request
  (`service="github"`), cached per `profile:sha256(token)` until shortly before it expires,
  with one forced refresh on a GitHub 401.
- RFC 9457 problems with the family's codes; GitHub rate limits as 429 with `Retry-After`;
  a repository the credential cannot see as 404, never 403.
- Subscriptions (`checks_settled`, `pull_merged`, `review_submitted`, `run_completed`) in
  SQLite, evaluated at subscribe time, by a background poller under the credential held from
  subscribe time, and on every `GET` from the hub's sweep; each ends with one signal sent by
  `lucy_signals.deliver`, signed with the subscription's secret.

### Changed

- `lucy-signals` comes from the LUCY-assistant tag `lucy-signals-v0.1.0`. It was pinned to
  the branch that added it, which has been merged and deleted; the package is unchanged.
