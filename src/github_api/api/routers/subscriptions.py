"""Subscriptions: the sibling side of Lucy's jobs contract (``docs/jobs.md``)."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Response, status

from github_api.api.dependencies import AccessDep, CallerDep, SubscriptionsDep
from github_api.api.schemas import NewSubscriptionBody, SubscriptionCreated, SubscriptionView
from github_api.credentials.keyring import CredentialError
from github_api.jobs.models import Subscription
from github_api.jobs.service import NewSubscription

router = APIRouter(prefix="/v1/subscriptions", tags=["subscriptions"])


def _when(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, UTC).isoformat()


def _view(sub: Subscription) -> SubscriptionView:
    """What a caller may read back. Never the signal URL, never the secret."""
    return SubscriptionView(
        id=sub.id,
        state=sub.state,
        kind=sub.kind,
        repo=sub.repo,
        target=sub.target,
        expires_at=_when(sub.expires_at),
        summary=sub.summary,
        facts=sub.facts,
        excerpt=sub.excerpt,
    )


@router.post("", response_model=SubscriptionCreated, status_code=status.HTTP_201_CREATED)
async def subscribe(
    *,
    body: NewSubscriptionBody,
    caller: CallerDep,
    access: AccessDep,
    subscriptions: SubscriptionsDep,
) -> SubscriptionCreated:
    """Start watching. The condition is looked at once now, and then until it holds."""
    expires_at = body.expires_at
    if expires_at is not None and expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    spec = NewSubscription(
        repo=body.repo,
        kind=body.kind,
        target=body.target,
        signal_url=body.signal.url,
        secret=body.signal.secret,
        expires_in_seconds=body.expires_in_seconds,
        expires_at=expires_at.timestamp() if expires_at is not None else None,
    )
    sub = await access.run(
        caller,
        lambda cred: subscriptions.create(caller.account_id, caller.profile, cred, spec),
        repo=body.repo,
    )
    return SubscriptionCreated(id=sub.id, state=sub.state, expires_at=_when(sub.expires_at))


@router.get("/{subscription_id}", response_model=SubscriptionView)
async def read_subscription(
    *,
    subscription_id: str,
    caller: CallerDep,
    access: AccessDep,
    subscriptions: SubscriptionsDep,
) -> SubscriptionView:
    """Where it stands -- and, while it runs, one look now under the caller's credential.

    The hub's sweep calls this under its standing grant, which is how a subscription whose
    held credential lapsed (or that outlived a restart) is still driven to its end. A
    credential that cannot be had right now leaves the stored state as the answer.
    """
    sub = subscriptions.owned(caller.account_id, subscription_id)
    try:
        sub = await access.run(caller, lambda cred: subscriptions.refresh(sub, cred))
    except CredentialError:
        sub = subscriptions.owned(caller.account_id, subscription_id)
    return _view(sub)


@router.delete("/{subscription_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_subscription(
    *, subscription_id: str, caller: CallerDep, subscriptions: SubscriptionsDep
) -> Response:
    """Stop looking. Another account's subscription, or one already gone, is a 404."""
    subscriptions.delete(caller.account_id, subscription_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
