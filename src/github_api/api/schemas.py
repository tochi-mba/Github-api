"""Request bodies and the responses that are not GitHub projections.

Bodies refuse fields they do not name (``extra="forbid"``), so a misspelt field is a 422
naming it rather than a value silently ignored.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from github_api.api.dependencies import REPO


class Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LivenessResponse(BaseModel):
    status: str = Field(description="Always 'alive'.")
    version: str
    environment: str
    uptime_seconds: float


class CheckResult(BaseModel):
    status: str
    detail: dict[str, object] = Field(default_factory=dict)


class ReadyResponse(BaseModel):
    status: str
    version: str
    environment: str
    uptime_seconds: float
    checks: dict[str, CheckResult]


class NewRepo(Body):
    name: str = Field(pattern=r"^[A-Za-z0-9._-]{1,100}$")
    owner: str = ""
    visibility: Literal["private", "public", "internal"] = "private"
    description: str = ""


class RepoChanges(Body):
    visibility: Literal["private", "public", "internal"] | None = None
    description: str | None = None
    default_branch: str | None = None
    archived: bool | None = None

    @model_validator(mode="after")
    def _something(self) -> Self:
        if not self.model_dump(exclude_none=True):
            msg = "say what to change: `visibility`, `description`, `default_branch` or `archived`"
            raise ValueError(msg)
        return self


class NewPull(Body):
    title: str = Field(min_length=1)
    head: str = Field(min_length=1)
    base: str = ""
    body: str = ""
    draft: bool = False


class PullChanges(Body):
    title: str | None = Field(default=None, min_length=1)
    body: str | None = None
    draft: bool | None = None
    state: Literal["open", "closed"] | None = None

    @model_validator(mode="after")
    def _something(self) -> Self:
        if not self.model_dump(exclude_none=True):
            msg = "say what to change: `title`, `body`, `draft` or `state`"
            raise ValueError(msg)
        return self


class MergeRequest(Body):
    method: Literal["merge", "squash", "rebase"] = "squash"
    delete_branch: bool = False


class ReviewRequest(Body):
    event: Literal["approve", "request_changes", "comment"]
    body: str = ""

    @model_validator(mode="after")
    def _reasoned(self) -> Self:
        if self.event != "approve" and not self.body.strip():
            msg = "a review that requests changes or comments needs a `body` saying why"
            raise ValueError(msg)
        return self


class CommentRequest(Body):
    body: str = Field(min_length=1)


class NewIssue(Body):
    title: str = Field(min_length=1)
    body: str = ""
    labels: list[str] = Field(default_factory=list, max_length=20)


class IssueChanges(Body):
    state: Literal["open", "closed"]


class RerunRequest(Body):
    failed_only: bool = False


class DispatchRequest(Body):
    ref: str = Field(min_length=1)
    inputs: dict[str, str] = Field(default_factory=dict, max_length=25)


class CommitFile(Body):
    path: str = Field(min_length=1)
    content: str


class CommitRequest(Body):
    branch: str = Field(min_length=1)
    message: str = Field(min_length=1)
    files: list[CommitFile] = Field(min_length=1, max_length=100)
    base: str = ""


class NewBranch(Body):
    name: str = Field(min_length=1)
    start: str = ""


class SignalTarget(Body):
    url: str = Field(min_length=1)
    secret: str = Field(min_length=1, repr=False)


class NewSubscriptionBody(Body):
    repo: str = Field(pattern=REPO)
    kind: str
    target: dict[str, Any] = Field(default_factory=dict)
    expires_in_seconds: float | None = Field(default=None, gt=0)
    expires_at: datetime | None = None
    signal: SignalTarget


class SubscriptionCreated(BaseModel):
    id: str
    state: str
    expires_at: str


class SubscriptionView(BaseModel):
    id: str
    state: str
    kind: str
    repo: str
    target: dict[str, Any]
    expires_at: str
    summary: str = ""
    facts: dict[str, Any] = Field(default_factory=dict)
    excerpt: str = ""
