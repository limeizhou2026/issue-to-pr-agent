"""
Runs the whole pipeline — explore, plan, edit, test, retry-on-failure,
dry-run PR — against a copy of examples/demo_repo. The only thing faked is
the LLM call itself (via FakePlanner); everything else (file I/O, the
sandbox, the test runner, the git diff) is exercised for real, the same way
a `python -m src.cli` run against this exact example would behave.
"""
import shutil
from pathlib import Path

from git import Repo

from src.agent import build_graph
from src.llm import FakePlanner, FileEdit, PlanResponse
from src.state import initial_state
from src.tools.github_tools import open_pull_request

DEMO_REPO = Path(__file__).parent.parent / "examples" / "demo_repo"


def _clone_demo_repo(tmp_path) -> Path:
    work = tmp_path / "demo"
    shutil.copytree(DEMO_REPO, work)

    repo = Repo.init(work)
    with repo.config_writer() as cfg:
        cfg.set_value("user", "name", "Test User")
        cfg.set_value("user", "email", "test@example.com")
    # Skip git's own metadata: newer GitPython refuses to stage paths under .git/.
    repo.index.add(
        [
            str(p.relative_to(work))
            for p in work.rglob("*")
            if p.is_file() and ".git" not in p.relative_to(work).parts
        ]
    )
    repo.index.commit("initial commit")

    return work


def test_agent_fixes_the_demo_repos_discount_bug_end_to_end(tmp_path):
    repo_path = _clone_demo_repo(tmp_path)
    issue_text = (DEMO_REPO / "issue.txt").read_text()
    title, _, body = issue_text.partition("\n")

    correct_fix = PlanResponse(
        plan="Divide percent by 100 before applying the discount.",
        edits=[
            FileEdit(
                path="discount.py",
                content=(
                    "def apply_discount(price: float, percent: float) -> float:\n"
                    '    """Return the price after applying a percentage discount."""\n'
                    "    return price - (price * percent / 100)\n"
                ),
            )
        ],
    )
    planner = FakePlanner(responses=[correct_fix])

    graph = build_graph(planner)
    result = graph.invoke(
        initial_state(
            repo_path=str(repo_path),
            issue_title=title.strip(),
            issue_body=body.strip(),
            max_iterations=3,
        )
    )

    assert result["tests_passed"] is True
    assert result["iteration"] == 1

    # And the dry-run PR step reports the real diff without pushing anything.
    pr = open_pull_request(
        local_repo_path=str(repo_path),
        repo_full_name="octocat/example",
        base_branch="main",
        branch_name="agent/fix",
        commit_message=f"Fix: {title}",
        pr_title=f"Fix: {title}",
        pr_body=result["plan"],
        token=None,
        allow_pr=False,
    )

    assert pr.dry_run is True
    assert "percent / 100" in pr.diff

    # The committed example itself must be untouched by running this test.
    assert "price * percent)" in (DEMO_REPO / "discount.py").read_text()


def test_agent_gives_up_cleanly_when_the_fix_never_works(tmp_path):
    repo_path = _clone_demo_repo(tmp_path)
    issue_text = (DEMO_REPO / "issue.txt").read_text()
    title, _, body = issue_text.partition("\n")

    wrong_fix = PlanResponse(
        plan="A fix that doesn't actually address the bug.",
        edits=[FileEdit(path="discount.py", content="def apply_discount(price, percent):\n    return price\n")],
    )
    planner = FakePlanner(responses=[wrong_fix, wrong_fix])

    graph = build_graph(planner)
    result = graph.invoke(
        initial_state(
            repo_path=str(repo_path),
            issue_title=title.strip(),
            issue_body=body.strip(),
            max_iterations=2,
        )
    )

    assert result["tests_passed"] is False
    assert result["iteration"] == 2

    # Even in dry-run mode, a failing fix is reported but never staged as if
    # it were ready to push — the caller (src/cli.py) treats this as exit 1.
    pr = open_pull_request(
        local_repo_path=str(repo_path),
        repo_full_name="octocat/example",
        base_branch="main",
        branch_name="agent/fix",
        commit_message=f"Fix: {title}",
        pr_title=f"Fix: {title}",
        pr_body=result["plan"],
        token=None,
        allow_pr=False,
    )
    assert pr.dry_run is True
