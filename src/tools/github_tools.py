"""
GitHub-facing tools: reading an issue, and turning a local diff into a PR.

Opening a pull request is a real, externally-visible action, so it's gated
behind `allow_pr` and defaults to a dry run. In dry-run mode this never
pushes a branch or calls the GitHub API — it only inspects the local git
diff and reports what *would* be sent, so the agent is safe to run
repeatedly against a real repo while you're still trusting its output.
"""
from __future__ import annotations

from dataclasses import dataclass

from git import Repo
from github import Auth, Github


@dataclass
class PrResult:
    dry_run: bool
    branch: str
    title: str
    body: str
    diff: str
    url: str | None = None


def fetch_issue(repo_full_name: str, issue_number: int, token: str) -> dict:
    """Read-only: fetch an issue's title and body."""
    gh = Github(auth=Auth.Token(token))
    issue = gh.get_repo(repo_full_name).get_issue(issue_number)
    return {"title": issue.title, "body": issue.body or ""}


def open_pull_request(
    *,
    local_repo_path: str,
    repo_full_name: str,
    base_branch: str,
    branch_name: str,
    commit_message: str,
    pr_title: str,
    pr_body: str,
    token: str | None,
    allow_pr: bool,
) -> PrResult:
    repo = Repo(local_repo_path)
    diff = repo.git.diff()

    if not diff:
        return PrResult(dry_run=True, branch=branch_name, title=pr_title, body=pr_body, diff="")

    if not allow_pr:
        # Stop here on purpose: no push, no GitHub API call.
        return PrResult(dry_run=True, branch=branch_name, title=pr_title, body=pr_body, diff=diff)

    if not token:
        raise ValueError("GITHUB_TOKEN is required to open a real pull request.")

    repo.git.checkout("-b", branch_name)
    repo.git.add(A=True)
    repo.index.commit(commit_message)
    repo.remote(name="origin").push(refspec=f"{branch_name}:{branch_name}")

    gh = Github(auth=Auth.Token(token))
    pr = gh.get_repo(repo_full_name).create_pull(
        title=pr_title, body=pr_body, head=branch_name, base=base_branch
    )

    return PrResult(
        dry_run=False, branch=branch_name, title=pr_title, body=pr_body, diff=diff, url=pr.html_url
    )
