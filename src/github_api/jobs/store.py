"""Subscriptions, durably, in SQLite.

One table, one row per subscription. The signal secret is stored as it was given, because an
HMAC key has to be held to sign with it; it authorises ending one subscription at the hub and
nothing else, and it is never returned by a route or written to a log.

No credential is ever stored here. The poller holds the credential it was given at subscribe
time in memory only (see ``jobs/service.py``); after a restart the hub's sweep drives
evaluation until somebody presents a credential again.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from github_api.jobs.models import RUNNING, Subscription

MEMORY = ":memory:"

SCHEMA = """
CREATE TABLE IF NOT EXISTS subscriptions (
    id          TEXT PRIMARY KEY,
    account_id  TEXT NOT NULL,
    profile     TEXT NOT NULL,
    repo        TEXT NOT NULL,
    kind        TEXT NOT NULL,
    target      TEXT NOT NULL,
    signal_url  TEXT NOT NULL,
    secret      TEXT NOT NULL,
    state       TEXT NOT NULL,
    created_at  REAL NOT NULL,
    expires_at  REAL NOT NULL,
    summary     TEXT NOT NULL DEFAULT '',
    facts       TEXT NOT NULL DEFAULT '{}',
    excerpt     TEXT NOT NULL DEFAULT '',
    baseline    INTEGER NOT NULL DEFAULT -1,
    delivery    TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS subscriptions_account ON subscriptions (account_id, state);
CREATE INDEX IF NOT EXISTS subscriptions_state ON subscriptions (state, expires_at);
"""

COLUMNS = (
    "id",
    "account_id",
    "profile",
    "repo",
    "kind",
    "target",
    "signal_url",
    "secret",
    "state",
    "created_at",
    "expires_at",
    "summary",
    "facts",
    "excerpt",
    "baseline",
    "delivery",
)


class SubscriptionStore:
    """The ``subscriptions`` table. Every method is one statement; SQLite is the lock."""

    def __init__(self, path: str) -> None:
        if path != MEMORY:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        self._db.executescript(SCHEMA)

    def close(self) -> None:
        self._db.close()

    def healthy(self) -> bool:
        try:
            self._db.execute("SELECT 1").fetchone()
        except sqlite3.Error:
            return False
        return True

    def add(self, sub: Subscription) -> None:
        values = _row(sub)
        placeholders = ", ".join("?" for _ in COLUMNS)
        self._db.execute(
            f"INSERT INTO subscriptions ({', '.join(COLUMNS)}) VALUES ({placeholders})",  # noqa: S608 - fixed column names
            [values[column] for column in COLUMNS],
        )

    def get(self, sub_id: str) -> Subscription | None:
        row = self._db.execute("SELECT * FROM subscriptions WHERE id = ?", (sub_id,)).fetchone()
        return _subscription(row) if row is not None else None

    def running(self) -> list[Subscription]:
        rows = self._db.execute(
            "SELECT * FROM subscriptions WHERE state = ? ORDER BY expires_at", (RUNNING,)
        ).fetchall()
        return [_subscription(row) for row in rows]

    def count_running(self, account_id: str) -> int:
        row = self._db.execute(
            "SELECT COUNT(*) FROM subscriptions WHERE account_id = ? AND state = ?",
            (account_id, RUNNING),
        ).fetchone()
        return int(row[0])

    def save(self, sub: Subscription) -> None:
        """Write back what can change: the state and what it says, the baseline, the delivery."""
        values = _row(sub)
        self._db.execute(
            "UPDATE subscriptions SET state = ?, summary = ?, facts = ?, excerpt = ?, "
            "baseline = ?, delivery = ? WHERE id = ?",
            [
                values[column]
                for column in ("state", "summary", "facts", "excerpt", "baseline", "delivery", "id")
            ],
        )

    def delete(self, sub_id: str) -> None:
        self._db.execute("DELETE FROM subscriptions WHERE id = ?", (sub_id,))

    def purge(self, ended_before: float) -> int:
        """Forget ended subscriptions whose lifetime finished before ``ended_before``."""
        cursor = self._db.execute(
            "DELETE FROM subscriptions WHERE state != ? AND expires_at < ?",
            (RUNNING, ended_before),
        )
        return cursor.rowcount


def _row(sub: Subscription) -> dict[str, Any]:
    return {
        "id": sub.id,
        "account_id": sub.account_id,
        "profile": sub.profile,
        "repo": sub.repo,
        "kind": sub.kind,
        "target": json.dumps(sub.target, sort_keys=True),
        "signal_url": sub.signal_url,
        "secret": sub.secret,
        "state": sub.state,
        "created_at": sub.created_at,
        "expires_at": sub.expires_at,
        "summary": sub.summary,
        "facts": json.dumps(sub.facts, sort_keys=True),
        "excerpt": sub.excerpt,
        "baseline": sub.baseline,
        "delivery": sub.delivery,
    }


def _subscription(row: sqlite3.Row) -> Subscription:
    return Subscription(
        id=row["id"],
        account_id=row["account_id"],
        profile=row["profile"],
        repo=row["repo"],
        kind=row["kind"],
        target=json.loads(row["target"]),
        signal_url=row["signal_url"],
        secret=row["secret"],
        state=row["state"],
        created_at=row["created_at"],
        expires_at=row["expires_at"],
        summary=row["summary"],
        facts=json.loads(row["facts"]),
        excerpt=row["excerpt"],
        baseline=row["baseline"],
        delivery=row["delivery"],
    )


__all__ = ["MEMORY", "SubscriptionStore"]
