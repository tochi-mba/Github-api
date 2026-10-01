"""Whether a subscription's condition holds: one look, under one credential.

Each kind is one function from GitHub's current state to a :class:`Verdict`. A verdict says
*that* something happened -- CI settled and how, a merge, a review -- and never carries the
result: the woken turn reads that through the capability, framed as untrusted.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from github_api.jobs.models import FAILED, FIRED, NO_BASELINE, RUNNING, Subscription, Verdict

if TYPE_CHECKING:
    from github_api.github.models import GitHubCredential
    from github_api.github.protocols import GitHubGateway

PASSING = frozenset({"success", "neutral", "skipped"})
EXCERPT_CHARS = 1_500
COMPLETED = "completed"

REVIEW_WORDS = {
    "approved": "approved",
    "changes_requested": "requested changes on",
    "commented": "commented on",
    "dismissed": "dismissed a review on",
}


async def evaluate(gateway: GitHubGateway, cred: GitHubCredential, sub: Subscription) -> Verdict:
    """One look at GitHub for ``sub``. Raises whatever GitHub raised."""
    if sub.kind == "checks_settled":
        return await _checks_settled(gateway, cred, sub)
    if sub.kind == "pull_merged":
        return await _pull_merged(gateway, cred, sub)
    if sub.kind == "review_submitted":
        return await _review_submitted(gateway, cred, sub)
    return await _run_completed(gateway, cred, sub)


async def _checks_settled(
    gateway: GitHubGateway, cred: GitHubCredential, sub: Subscription
) -> Verdict:
    number = sub.target.get("number")
    ref = f"pull/{number}" if number is not None else str(sub.target["ref"])
    found = (await gateway.checks(cred, sub.repo, ref)).checks
    if not found or any(check.status != COMPLETED for check in found):
        return Verdict(state=RUNNING)
    failed = [check for check in found if check.conclusion not in PASSING]
    conclusion = "failure" if failed else "success"
    summary = (
        f"CI on {sub.label} failed: {len(failed)} of {len(found)} checks"
        if failed
        else f"CI on {sub.label} passed"
    )
    excerpt = "\n".join(
        f"{check.name}: {check.conclusion or 'none'}"
        + (f" ({', '.join(check.failing_steps)})" if check.failing_steps else "")
        for check in failed
    )
    return Verdict(
        state=FIRED,
        summary=summary,
        facts={"conclusion": conclusion, "checks": len(found), "failed": len(failed)},
        excerpt=excerpt[:EXCERPT_CHARS],
    )


async def _pull_merged(
    gateway: GitHubGateway, cred: GitHubCredential, sub: Subscription
) -> Verdict:
    number = int(sub.target["number"])
    pull = await gateway.pull_summary(cred, sub.repo, number)
    if pull.state == "merged":
        return Verdict(state=FIRED, summary=f"{sub.label} was merged", facts={"number": number})
    if pull.state == "closed":
        return Verdict(
            state=FAILED,
            summary=f"{sub.label} was closed without merging",
            facts={"number": number},
        )
    return Verdict(state=RUNNING)


async def _review_submitted(
    gateway: GitHubGateway, cred: GitHubCredential, sub: Subscription
) -> Verdict:
    found = await gateway.latest_review(cred, sub.repo, int(sub.target["number"]))
    if sub.baseline == NO_BASELINE or found.total <= sub.baseline or found.latest is None:
        baseline = found.total if sub.baseline == NO_BASELINE else None
        return Verdict(state=RUNNING, baseline=baseline)
    review = found.latest
    words = REVIEW_WORDS.get(review.state, "reviewed")
    return Verdict(
        state=FIRED,
        summary=f"{review.author} {words} {sub.label}",
        facts={"author": review.author, "state": review.state},
        excerpt=review.body[:EXCERPT_CHARS],
        baseline=found.total,
    )


async def _run_completed(
    gateway: GitHubGateway, cred: GitHubCredential, sub: Subscription
) -> Verdict:
    status = await gateway.run_status(cred, sub.repo, str(sub.target["run"]))
    if status.status != COMPLETED:
        return Verdict(state=RUNNING)
    conclusion = status.conclusion or "none"
    return Verdict(
        state=FIRED,
        summary=f"{status.name or sub.label} finished: {conclusion}",
        facts={"conclusion": conclusion, "name": status.name},
    )


__all__ = ["evaluate"]
