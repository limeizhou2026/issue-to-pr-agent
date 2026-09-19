"""
Entry point: fix an issue in a local repo, optionally opening a real PR.

    python -m src.cli --repo /path/to/repo --issue-file issue.txt
    python -m src.cli --repo /path/to/repo --issue-number 42 --repo-full-name owner/repo --allow-pr

By default this is a dry run: it prints the diff it would submit but never
pushes a branch or calls the GitHub API. Add --allow-pr to actually open a
pull request — and even then, it only does so if the fix's tests passed.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from src.agent import build_graph
from src.llm import ClaudePlanner
from src.state import initial_state
from src.tools.github_tools import fetch_issue, open_pull_request


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, help="Path to the local git repo to work on.")
    parser.add_argument(
        "--issue-file", help="Text file: first line is the issue title, the rest is the body."
    )
    parser.add_argument("--issue-title")
    parser.add_argument("--issue-body")
    parser.add_argument("--issue-number", type=int, help="Fetch this issue from GitHub instead.")
    parser.add_argument("--repo-full-name", help="e.g. octocat/example — required with --issue-number.")
    parser.add_argument("--base-branch", default="main")
    parser.add_argument("--branch-name", default="agent/fix")
    parser.add_argument("--max-iterations", type=int, default=3)
    parser.add_argument(
        "--allow-pr",
        action="store_true",
        help="Actually push a branch and open a PR (default: dry run only).",
    )
    return parser.parse_args(argv)


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise SystemExit(f"{name} is required (set it in .env or the environment).")
    return value


def load_issue(args: argparse.Namespace) -> tuple[str, str]:
    if args.issue_number:
        if not args.repo_full_name:
            raise SystemExit("--repo-full-name is required together with --issue-number")
        issue = fetch_issue(args.repo_full_name, args.issue_number, _require_env("GITHUB_TOKEN"))
        return issue["title"], issue["body"]

    if args.issue_file:
        title, _, body = Path(args.issue_file).read_text().partition("\n")
        return title.strip(), body.strip()

    if args.issue_title and args.issue_body is not None:
        return args.issue_title, args.issue_body

    raise SystemExit(
        "Provide one of: --issue-file, --issue-title/--issue-body, "
        "or --issue-number/--repo-full-name"
    )


def run(args: argparse.Namespace) -> int:
    issue_title, issue_body = load_issue(args)

    graph = build_graph(ClaudePlanner())
    result = graph.invoke(
        initial_state(
            repo_path=args.repo,
            issue_title=issue_title,
            issue_body=issue_body,
            max_iterations=args.max_iterations,
        )
    )

    print("\n".join(result["log"]))
    print()

    # Opening a real PR is gated on the fix's own tests having passed — even
    # with --allow-pr, an unverified change is never pushed.
    allow_pr = args.allow_pr and result["tests_passed"]

    pr = open_pull_request(
        local_repo_path=args.repo,
        repo_full_name=args.repo_full_name or "",
        base_branch=args.base_branch,
        branch_name=args.branch_name,
        commit_message=f"Fix: {issue_title}",
        pr_title=f"Fix: {issue_title}",
        pr_body=result["plan"],
        token=os.environ.get("GITHUB_TOKEN"),
        allow_pr=allow_pr,
    )

    if not result["tests_passed"]:
        print(f"Gave up after {result['iteration']} iteration(s) without a passing test run.")
        print("\nDiff from the last (failing) attempt — not pushed:\n")
        print(pr.diff or "(no changes)")
        return 1

    print(f"Tests passed after {result['iteration']} iteration(s).")
    if pr.dry_run:
        print("\nDRY RUN — no branch pushed, no PR opened. Diff:\n")
        print(pr.diff or "(no changes)")
    else:
        print(f"\nOpened PR: {pr.url}")

    return 0


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    args = parse_args(argv)

    try:
        return run(args)
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001 — CLI boundary: show a clean message, not a traceback
        print(f"issue-to-pr-agent failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
