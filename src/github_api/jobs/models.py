"""What a subscription is, and the rules a new one must satisfy.

The kinds are the hub's (``repos.watch``'s ``until``): ``checks_settled`` (a pull request's
or a ref's CI has finished), ``pull_merged``, ``review_submitted`` (a new review lands on a
pull request) and ``run_completed`` (one CI job, by the id the checks give it, finishes).
The names are the hub's ``WATCH_KINDS``, verbatim.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

KINDS = ("checks_settled", "pull_merged", "review_submitted", "run_completed")

RUNNING = "running"
FIRED = "fired"
FAILED = "failed"
EXPIRED = "expired"
STATES = (RUNNING, FIRED, FAILED, EXPIRED)

NO_BASELINE = -1
"""``review_submitted`` has not counted the reviews it is waiting past yet."""

TARGET_KEYS = ("number", "ref", "run")
NEEDS = {
    "checks_settled": "exactly one of `number` (a pull request) or `ref` (a branch or commit)",
    "pull_merged": "`number`, the pull request",
    "review_submitted": "`number`, the pull request",
    "run_completed": "`run`, a job id as the checks give it",
}


class SubscriptionRefusedError(Exception):
    """A subscription that cannot be opened as asked. ``str(exc)`` says what would work."""

    def __init__(self, message: str, *, code: str = "invalid-subscription") -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class Subscription:
    """One durable watch. ``secret`` signs the ending signal and is never shown."""

    id: str
    account_id: str
    profile: str
    repo: str
    kind: str
    target: dict[str, Any]
    signal_url: str = field(repr=False)
    secret: str = field(repr=False)
    state: str
    created_at: float
    expires_at: float
    summary: str = ""
    facts: dict[str, Any] = field(default_factory=dict)
    excerpt: str = ""
    baseline: int = NO_BASELINE
    delivery: str = ""

    @property
    def label(self) -> str:
        """What is being watched, as a person would write it."""
        if "number" in self.target:
            return f"{self.repo}#{self.target['number']}"
        if "ref" in self.target:
            return f"{self.repo}@{self.target['ref']}"
        return f"{self.repo} job {self.target.get('run', '')}"


@dataclass(frozen=True, slots=True)
class Verdict:
    """What one look found. ``running`` with a ``baseline`` records a count to wait past."""

    state: str
    summary: str = ""
    facts: dict[str, Any] = field(default_factory=dict)
    excerpt: str = ""
    baseline: int | None = None


def kind_of(raw: str) -> str:
    """The canonical kind, or a refusal naming every kind this service knows."""
    kind = raw
    if kind not in KINDS:
        message = f"unknown kind `{raw}`; this service knows {', '.join(f'`{k}`' for k in KINDS)}"
        raise SubscriptionRefusedError(message, code="unknown-kind")
    return kind


def target_for(kind: str, raw: dict[str, Any]) -> dict[str, Any]:
    """The target, checked against what ``kind`` needs."""
    unknown = sorted(set(raw) - set(TARGET_KEYS))
    if unknown:
        message = f"unknown target field `{unknown[0]}`; a target has " + ", ".join(
            f"`{k}`" for k in TARGET_KEYS
        )
        raise SubscriptionRefusedError(message)
    target: dict[str, Any] = {}
    number = raw.get("number")
    if number is not None:
        if isinstance(number, bool) or not isinstance(number, int) or number < 1:
            message = "`target.number` must be a pull request number, 1 or more"
            raise SubscriptionRefusedError(message)
        target["number"] = number
    for key in ("ref", "run"):
        value = raw.get(key)
        if value not in (None, ""):
            target[key] = str(value)
    if not _fits(kind, target):
        message = f"`{kind}` needs {NEEDS[kind]} in `target`"
        raise SubscriptionRefusedError(message)
    return target


def _fits(kind: str, target: dict[str, Any]) -> bool:
    if kind == "checks_settled":
        return set(target) in ({"number"}, {"ref"})
    if kind == "run_completed":
        return set(target) == {"run"}
    return set(target) == {"number"}


__all__ = [
    "EXPIRED",
    "FAILED",
    "FIRED",
    "KINDS",
    "NO_BASELINE",
    "RUNNING",
    "STATES",
    "Subscription",
    "SubscriptionRefusedError",
    "Verdict",
    "kind_of",
    "target_for",
]
