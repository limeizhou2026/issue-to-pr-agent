import subprocess

import pytest

from src.tools import repo_tools


class TestResolveSafePath:
    def test_allows_a_normal_relative_path(self, tmp_path):
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "app.py").write_text("x = 1")

        resolved = repo_tools.resolve_safe_path(tmp_path, "src/app.py")
        assert resolved == (tmp_path / "src" / "app.py").resolve()

    def test_rejects_dot_dot_traversal(self, tmp_path):
        with pytest.raises(repo_tools.SandboxError):
            repo_tools.resolve_safe_path(tmp_path, "../../etc/passwd")

    def test_rejects_an_absolute_path_escape(self, tmp_path):
        with pytest.raises(repo_tools.SandboxError):
            repo_tools.resolve_safe_path(tmp_path, "/etc/passwd")

    def test_rejects_a_symlink_that_escapes_the_sandbox(self, tmp_path):
        outside = tmp_path.parent / "outside_secret.txt"
        outside.write_text("secret")
        (tmp_path / "link.txt").symlink_to(outside)

        with pytest.raises(repo_tools.SandboxError):
            repo_tools.resolve_safe_path(tmp_path, "link.txt")

        outside.unlink()


class TestListFiles:
    def test_lists_files_and_skips_excluded_directories(self, tmp_path):
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "app.py").write_text("x = 1")
        (tmp_path / "node_modules").mkdir()
        (tmp_path / "node_modules" / "junk.js").write_text("noise")
        (tmp_path / ".git").mkdir()
        (tmp_path / ".git" / "HEAD").write_text("ref: refs/heads/main")

        files = repo_tools.list_files(tmp_path)

        assert "src/app.py" in files
        assert not any(f.startswith("node_modules") for f in files)
        assert not any(f.startswith(".git") for f in files)

    def test_filters_by_extension(self, tmp_path):
        (tmp_path / "a.py").write_text("1")
        (tmp_path / "b.md").write_text("2")

        files = repo_tools.list_files(tmp_path, extensions=(".py",))

        assert files == ["a.py"]


class TestReadWriteFile:
    def test_write_then_read_round_trips(self, tmp_path):
        repo_tools.write_file(tmp_path, "nested/dir/file.txt", "hello")
        assert repo_tools.read_file(tmp_path, "nested/dir/file.txt") == "hello"

    def test_read_missing_file_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            repo_tools.read_file(tmp_path, "does_not_exist.txt")

    def test_read_truncates_at_max_bytes(self, tmp_path):
        (tmp_path / "big.txt").write_text("x" * 100)
        assert repo_tools.read_file(tmp_path, "big.txt", max_bytes=10) == "x" * 10

    def test_write_refuses_to_escape_the_sandbox(self, tmp_path):
        with pytest.raises(repo_tools.SandboxError):
            repo_tools.write_file(tmp_path, "../escaped.txt", "pwned")

        assert not (tmp_path.parent / "escaped.txt").exists()


class TestDetectTestCommand:
    def test_detects_pytest_from_pytest_ini(self, tmp_path):
        (tmp_path / "pytest.ini").write_text("[pytest]\n")
        assert repo_tools.detect_test_command(tmp_path)[:4] == ["python3", "-B", "-m", "pytest"]

    def test_detects_npm_from_package_json(self, tmp_path):
        (tmp_path / "package.json").write_text("{}")
        assert repo_tools.detect_test_command(tmp_path)[:2] == ["npm", "test"]

    def test_returns_none_when_no_runner_recognized(self, tmp_path):
        assert repo_tools.detect_test_command(tmp_path) is None

    @pytest.mark.parametrize(
        "marker, expected_tool",
        [
            ("go.mod", "go"),
            ("pom.xml", "mvn"),
            ("build.gradle", "gradle"),
            ("build.gradle.kts", "gradle"),
            (".rspec", "bundle"),
            ("Rakefile", "bundle"),
        ],
    )
    def test_detects_go_java_and_ruby_runners(self, tmp_path, marker, expected_tool):
        (tmp_path / marker).write_text("")
        assert repo_tools.detect_test_command(tmp_path)[0] == expected_tool

    def test_every_source_language_has_a_test_runner(self):
        """Guards against reading/editing a language the agent can't verify."""
        from src.agent import SOURCE_EXTENSIONS

        tools = {command[0] for _, command in repo_tools._TEST_RUNNERS}
        language_to_tool = {
            ".py": "python3", ".ts": "npm", ".tsx": "npm", ".js": "npm", ".jsx": "npm",
            ".go": "go", ".java": "mvn", ".rb": "bundle",
        }
        for ext in SOURCE_EXTENSIONS:
            assert ext in language_to_tool, f"no known test runner mapping for {ext}"
            assert language_to_tool[ext] in tools, f"{ext} files have no test runner"


class TestRunTests:
    def test_runs_a_passing_pytest_suite(self, tmp_path):
        (tmp_path / "pytest.ini").write_text("[pytest]\n")
        (tmp_path / "test_ok.py").write_text("def test_ok():\n    assert True\n")

        result = repo_tools.run_tests(tmp_path)

        assert result.passed is True
        assert "1 passed" in result.output

    def test_runs_a_failing_pytest_suite(self, tmp_path):
        (tmp_path / "pytest.ini").write_text("[pytest]\n")
        (tmp_path / "test_bad.py").write_text("def test_bad():\n    assert False\n")

        result = repo_tools.run_tests(tmp_path)

        assert result.passed is False
        assert "1 failed" in result.output

    def test_reports_when_no_test_runner_is_recognized(self, tmp_path):
        result = repo_tools.run_tests(tmp_path)
        assert result.passed is False
        assert "No recognized test runner" in result.output

    def test_rapid_same_size_rewrites_are_not_served_stale_bytecode(self, tmp_path):
        """
        Regression test: CPython's default .pyc invalidation keys on (mtime
        truncated to whole seconds, file size). Two different fixes of the
        same byte length, written within the same second, are indistinguishable
        from "unchanged" under that scheme — an agent iterating quickly on a
        fix could get a stale pass/fail verdict. `-B` (see _TEST_RUNNERS) is
        what prevents that; this test would fail without it.
        """
        (tmp_path / "pytest.ini").write_text("[pytest]\n")
        (tmp_path / "test_add.py").write_text(
            "from add_fn import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n"
        )

        # Same byte length as the fix below, wrong operator.
        repo_tools.write_file(tmp_path, "add_fn.py", "def add(a, b):\n    return b - a\n")
        first = repo_tools.run_tests(tmp_path)
        assert first.passed is False

        # Same byte length as above, correct operator — written immediately
        # after, likely within the same wall-clock second.
        repo_tools.write_file(tmp_path, "add_fn.py", "def add(a, b):\n    return a + b\n")
        second = repo_tools.run_tests(tmp_path)
        assert second.passed is True

    def test_handles_a_timeout_without_raising(self, tmp_path, monkeypatch):
        (tmp_path / "pytest.ini").write_text("[pytest]\n")

        def fake_run(*args, **kwargs):
            raise subprocess.TimeoutExpired(cmd="pytest", timeout=kwargs.get("timeout", 1))

        monkeypatch.setattr(repo_tools.subprocess, "run", fake_run)

        result = repo_tools.run_tests(tmp_path, timeout=1)

        assert result.passed is False
        assert "timed out" in result.output

    def test_reports_a_missing_tool_without_raising(self, tmp_path, monkeypatch):
        (tmp_path / "go.mod").write_text("module example\n")
        monkeypatch.setattr(
            repo_tools, "_TEST_RUNNERS", [("go.mod", ["definitely-not-an-installed-tool-xyz"])]
        )

        result = repo_tools.run_tests(tmp_path)

        assert result.passed is False
        assert "not installed" in result.output
