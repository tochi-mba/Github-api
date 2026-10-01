"""The fake gateway refuses the way GitHub does, and satisfies the same protocol."""

from __future__ import annotations

import pytest

from github_api.github.client import GitHubClient
from github_api.github.errors import GitHubConflictError, GitHubNotFoundError
from github_api.github.fake import FakeGateway
from github_api.github.http import GitHubHttp
from github_api.github.models import GitHubCredential, Pull, PullDetail, Repo
from github_api.github.protocols import GitHubGateway

CRED = GitHubCredential(headers={"Authorization": "Bearer ghu_x"})
REPO = "octo/hello"


@pytest.fixture
def fake() -> FakeGateway:
    found = FakeGateway()
    found.seed_repo(Repo(full_name=REPO, default_branch="main"))
    found.seed_pull(PullDetail(pull=Pull(repo=REPO, number=1, title="t", head="f")))
    return found


async def test_both_gateways_satisfy_the_protocol() -> None:
    gateways: list[GitHubGateway] = [
        FakeGateway(),
        GitHubClient(
            GitHubHttp(
                base_url="https://x.test", graphql_url="https://x.test/graphql", timeout_seconds=1
            ),
            file_chars_max=1,
            tree_entries_max=1,
            patch_chars_max=1,
        ),
    ]
    for gateway in gateways:
        await gateway.aclose()


async def test_missing_things_are_404(fake: FakeGateway) -> None:
    with pytest.raises(GitHubNotFoundError):
        await fake.raw_log(CRED, REPO, "1")
    with pytest.raises(GitHubNotFoundError):
        await fake.run_status(CRED, REPO, "1")
    with pytest.raises(GitHubNotFoundError):
        await fake.read_file(CRED, REPO, "a.py", ref="")
    with pytest.raises(GitHubNotFoundError):
        await fake.set_issue_state(CRED, REPO, 9, "closed")
    with pytest.raises(GitHubNotFoundError):
        await fake.create_branch(CRED, REPO, "x", start="nowhere")
    with pytest.raises(GitHubNotFoundError):
        await fake.pull(CRED, REPO, 99)


async def test_writes_change_what_reads_see(fake: FakeGateway) -> None:
    changed = await fake.update_repo(CRED, REPO, {"description": "d"})
    assert changed.description == "d"
    await fake.update_pull(CRED, REPO, 1, {"body": "ignored", "title": "new"})
    assert (await fake.pull_summary(CRED, REPO, 1)).title == "new"
    merged = await fake.merge(CRED, REPO, 1, method="merge", delete_branch=False)
    assert merged.merged
    await fake.commit(CRED, REPO, branch="main", message="m", files=[], base="")

    dirty = Pull(repo=REPO, number=2, title="t", mergeable="dirty")
    fake.seed_pull(PullDetail(pull=dirty))
    with pytest.raises(GitHubConflictError):
        await fake.merge(CRED, REPO, 2, method="merge", delete_branch=True)
    review = await fake.review(CRED, REPO, 1, event="comment", body="b")
    assert review.state == "commented"
