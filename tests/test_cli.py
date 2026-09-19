import pytest

from src.cli import load_issue, parse_args


def test_issue_file_splits_first_line_as_title(tmp_path):
    issue_file = tmp_path / "issue.txt"
    issue_file.write_text("Title line\nBody line one.\nBody line two.\n")

    args = parse_args(["--repo", "/tmp/repo", "--issue-file", str(issue_file)])
    title, body = load_issue(args)

    assert title == "Title line"
    assert body == "Body line one.\nBody line two."


def test_inline_title_and_body_are_used_directly():
    args = parse_args(
        ["--repo", "/tmp/repo", "--issue-title", "Bug title", "--issue-body", "Bug body"]
    )
    assert load_issue(args) == ("Bug title", "Bug body")


def test_issue_number_without_repo_full_name_exits():
    args = parse_args(["--repo", "/tmp/repo", "--issue-number", "5"])
    with pytest.raises(SystemExit):
        load_issue(args)


def test_no_issue_source_at_all_exits():
    args = parse_args(["--repo", "/tmp/repo"])
    with pytest.raises(SystemExit):
        load_issue(args)


def test_allow_pr_defaults_to_false():
    args = parse_args(["--repo", "/tmp/repo", "--issue-title", "t", "--issue-body", "b"])
    assert args.allow_pr is False
