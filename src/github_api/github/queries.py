"""The GraphQL documents this service sends.

GraphQL is used where it answers in one call what REST answers in many: a repository with
its open counts and default-branch CI, a pull request with its reviews, its review threads
(whose resolution REST cannot tell) and its checks with their failing steps.
"""

from __future__ import annotations

REPO_FIELDS = """
fragment RepoFields on Repository {
  nameWithOwner
  isPrivate
  description
  url
  defaultBranchRef { name target { ... on Commit { statusCheckRollup { state } } } }
  pullRequests(states: OPEN) { totalCount }
  issues(states: OPEN) { totalCount }
}
"""

PULL_FIELDS = """
fragment PullFields on PullRequest {
  number
  title
  state
  isDraft
  url
  headRefName
  baseRefName
  mergeStateStatus
  author { login }
  repository { nameWithOwner }
  commits(last: 1) { nodes { commit { statusCheckRollup { state } } } }
}
"""

CHECK_FIELDS = """
fragment CheckFields on StatusCheckRollup {
  state
  contexts(first: 100) {
    nodes {
      __typename
      ... on CheckRun {
        databaseId
        name
        status
        conclusion
        detailsUrl
        checkSuite { workflowRun { workflow { name } } }
        steps(first: 100) { nodes { name conclusion } }
      }
      ... on StatusContext { context state targetUrl }
    }
  }
}
"""

REPO = (
    REPO_FIELDS
    + """
query($owner: String!, $name: String!) {
  repository(owner: $owner, name: $name) { ...RepoFields }
}
"""
)

VIEWER_REPOS = (
    REPO_FIELDS
    + """
query($first: Int!) {
  viewer {
    repositories(
      first: $first
      orderBy: {field: PUSHED_AT, direction: DESC}
      affiliations: [OWNER, COLLABORATOR, ORGANIZATION_MEMBER]
    ) { totalCount nodes { ...RepoFields } }
  }
}
"""
)

SEARCH_REPOS = (
    REPO_FIELDS
    + """
query($q: String!, $first: Int!) {
  search(query: $q, type: REPOSITORY, first: $first) {
    repositoryCount
    nodes { ... on Repository { ...RepoFields } }
  }
}
"""
)

PULLS = (
    PULL_FIELDS
    + """
query($owner: String!, $name: String!, $states: [PullRequestState!], $first: Int!) {
  repository(owner: $owner, name: $name) {
    pullRequests(states: $states, first: $first, orderBy: {field: UPDATED_AT, direction: DESC}) {
      totalCount
      nodes { ...PullFields }
    }
  }
}
"""
)

PULL = (
    PULL_FIELDS
    + """
query($owner: String!, $name: String!, $number: Int!) {
  repository(owner: $owner, name: $name) { pullRequest(number: $number) { ...PullFields } }
}
"""
)

PULL_DETAIL = (
    PULL_FIELDS
    + CHECK_FIELDS
    + """
query($owner: String!, $name: String!, $number: Int!) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) {
      ...PullFields
      body
      reviews(last: 50) { nodes { author { login } state body } }
      reviewThreads(first: 100) {
        nodes {
          isResolved
          path
          line
          originalLine
          comments(first: 1) { nodes { author { login } body } }
        }
      }
      latest: commits(last: 1) { nodes { commit { statusCheckRollup { ...CheckFields } } } }
    }
  }
}
"""
)

LATEST_REVIEW = """
query($owner: String!, $name: String!, $number: Int!) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) {
      reviews(last: 1) { totalCount nodes { author { login } state body } }
    }
  }
}
"""

PULL_CHECKS = (
    CHECK_FIELDS
    + """
query($owner: String!, $name: String!, $number: Int!) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) {
      commits(last: 1) { nodes { commit { statusCheckRollup { ...CheckFields } } } }
    }
  }
}
"""
)

REF_CHECKS = (
    CHECK_FIELDS
    + """
query($owner: String!, $name: String!, $ref: String!) {
  repository(owner: $owner, name: $name) {
    object(expression: $ref) { ... on Commit { statusCheckRollup { ...CheckFields } } }
  }
}
"""
)

ISSUES = """
query($owner: String!, $name: String!, $states: [IssueState!], $first: Int!) {
  repository(owner: $owner, name: $name) {
    issues(states: $states, first: $first, orderBy: {field: UPDATED_AT, direction: DESC}) {
      totalCount
      nodes {
        number
        title
        state
        url
        author { login }
        repository { nameWithOwner }
        labels(first: 20) { nodes { name } }
      }
    }
  }
}
"""

TO_DRAFT = """
mutation($id: ID!) {
  done: convertPullRequestToDraft(input: {pullRequestId: $id}) { clientMutationId }
}
"""

READY_FOR_REVIEW = """
mutation($id: ID!) {
  done: markPullRequestReadyForReview(input: {pullRequestId: $id}) { clientMutationId }
}
"""
