from types import SimpleNamespace

import pytest
from git import Repo

from src.tools import github_tools


@pytest.fixture
def repo_with_remote(tmp_path):
    """A local repo with an initial commit, pushed to a local bare 'origin'."""
    bare_path = tmp_path / "origin.git"
    Repo.init(bare_path, bare=True)

    work_path = tmp_path / "work"
    repo = Repo.init(work_path)
    (work_path / "README.md").write_text("hello\n")
    repo.index.add(["README.md"])
    repo.index.commit("initial commit")
    with repo.config_writer() as cfg:
        cfg.set_value("user", "name", "Test User")
        cfg.set_value("user", "email", "test@example.com")

    default_branch = repo.active_branch.name
    origin = repo.create_remote("origin", str(bare_path))
    origin.push(refspec=f"{default_branch}:{default_branch}")

    return repo, work_path, default_branch


def test_dry_run_by_default_when_there_are_changes(repo_with_remote):
    repo, work_path, base = repo_with_remote
    (work_path / "README.md").write_text("hello, edited\n")

    result = github_tools.open_pull_request(
        local_repo_path=str(work_path),
        repo_full_name="octocat/example",
        base_branch=base,
        branch_name="agent/fix-1",
        commit_message="fix: something",
        pr_title="Fix something",
        pr_body="Because the tests said so.",
        token=None,
        allow_pr=False,
    )

    assert result.dry_run is True
    assert result.url is None
    assert "hello, edited" in result.diff
    # No branch should have been created in dry-run mode.
    assert "agent/fix-1" not in [h.name for h in repo.heads]


def test_reports_no_changes_when_working_tree_is_clean(repo_with_remote):
    _, work_path, base = repo_with_remote

    result = github_tools.open_pull_request(
        local_repo_path=str(work_path),
        repo_full_name="octocat/example",
        base_branch=base,
        branch_name="agent/fix-1",
        commit_message="fix: something",
        pr_title="Fix something",
        pr_body="body",
        token=None,
        allow_pr=False,
    )

    assert result.dry_run is True
    assert result.diff == ""


def test_allow_pr_without_token_raises(repo_with_remote):
    _, work_path, base = repo_with_remote
    (work_path / "README.md").write_text("changed\n")

    with pytest.raises(ValueError, match="GITHUB_TOKEN"):
        github_tools.open_pull_request(
            local_repo_path=str(work_path),
            repo_full_name="octocat/example",
            base_branch=base,
            branch_name="agent/fix-1",
            commit_message="fix: something",
            pr_title="Fix something",
            pr_body="body",
            token=None,
            allow_pr=True,
        )


def test_allow_pr_pushes_a_real_branch_and_calls_the_github_api(repo_with_remote, monkeypatch):
    repo, work_path, base = repo_with_remote
    (work_path / "README.md").write_text("changed for real\n")

    created_pulls = []

    class FakeRepo:
        def create_pull(self, *, title, body, head, base):
            created_pulls.append({"title": title, "body": body, "head": head, "base": base})
            return SimpleNamespace(html_url="https://github.com/octocat/example/pull/1")

    class FakeGithub:
        def __init__(self, *args, **kwargs):
            pass

        def get_repo(self, full_name):
            assert full_name == "octocat/example"
            return FakeRepo()

    monkeypatch.setattr(github_tools, "Github", FakeGithub)

    result = github_tools.open_pull_request(
        local_repo_path=str(work_path),
        repo_full_name="octocat/example",
        base_branch=base,
        branch_name="agent/fix-1",
        commit_message="fix: something",
        pr_title="Fix something",
        pr_body="body",
        token="fake-token",
        allow_pr=True,
    )

    assert result.dry_run is False
    assert result.url == "https://github.com/octocat/example/pull/1"
    assert created_pulls == [
        {"title": "Fix something", "body": "body", "head": "agent/fix-1", "base": base}
    ]
    # The branch was actually created and committed locally...
    assert "agent/fix-1" in [h.name for h in repo.heads]
    # ...and actually pushed to the (local, bare) 'origin' remote.
    bare = Repo(str(work_path.parent / "origin.git"))
    assert "agent/fix-1" in [h.name for h in bare.heads]


def test_fetch_issue_returns_title_and_body(monkeypatch):
    class FakeIssue:
        title = "Bug: off-by-one in pagination"
        body = "Page 2 skips the last item."

    class FakeRepo:
        def get_issue(self, number):
            assert number == 42
            return FakeIssue()

    class FakeGithub:
        def __init__(self, *args, **kwargs):
            pass

        def get_repo(self, full_name):
            assert full_name == "octocat/example"
            return FakeRepo()

    monkeypatch.setattr(github_tools, "Github", FakeGithub)

    result = github_tools.fetch_issue("octocat/example", 42, token="fake-token")

    assert result == {
        "title": "Bug: off-by-one in pagination",
        "body": "Page 2 skips the last item.",
    }
