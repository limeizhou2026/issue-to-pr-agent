import pytest

from src import cli
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


def test_run_exits_before_any_model_call_when_repo_has_no_test_runner(tmp_path, monkeypatch):
    (tmp_path / "main.go").write_text("package main\n")  # source code, but no go.mod

    def fail_if_called(*args, **kwargs):
        raise AssertionError("the model must not be called when fixes can't be verified")

    monkeypatch.setattr(cli, "ClaudePlanner", fail_if_called)
    args = parse_args(["--repo", str(tmp_path), "--issue-title", "t", "--issue-body", "b"])

    with pytest.raises(SystemExit, match="No supported test runner"):
        cli.run(args)

    assert (tmp_path / "main.go").read_text() == "package main\n"


def test_require_test_runner_passes_when_a_marker_exists(tmp_path):
    (tmp_path / "go.mod").write_text("module example\n")
    cli.require_test_runner(str(tmp_path))  # does not raise
