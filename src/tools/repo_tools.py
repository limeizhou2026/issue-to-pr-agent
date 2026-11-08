"""
File and test-runner tools the agent uses to inspect and modify a target repo.

The core security property these tools provide: every path the LLM supplies
is resolved *and verified* to stay inside the target repo before any file
I/O happens, and the test runner never executes a command built from LLM- or
issue-controlled text. An issue body is untrusted input (anyone can file a
GitHub issue) that ends up in the model's context — if that text could shape
a shell command or an unchecked file path, a malicious issue could walk the
agent into reading `~/.ssh/id_rsa` or running arbitrary code on the host.
Neither of those tools takes that risk: paths are sandboxed, and the test
command is chosen by *us*, from a fixed allowlist, based on what files exist
in the repo — never from free-form text.
"""
from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

EXCLUDED_DIRS = {
    ".git", "__pycache__", ".venv", "venv", "node_modules",
    "dist", "build", ".next", "cdk.out", ".pytest_cache", ".mypy_cache",
}

MAX_READ_BYTES = 40_000


class SandboxError(ValueError):
    """Raised when a tool is asked to touch a path outside the target repo."""


def resolve_safe_path(repo_root: str | Path, relative_path: str) -> Path:
    """
    Resolve `relative_path` against `repo_root` and guarantee the result is
    still inside it. Rejects absolute paths, `..` traversal, and symlinks
    that resolve outside the sandbox.
    """
    root = Path(repo_root).resolve()
    candidate = (root / relative_path).resolve()

    try:
        candidate.relative_to(root)
    except ValueError:
        raise SandboxError(
            f"Refusing to touch path outside the repo sandbox: {relative_path!r}"
        )

    return candidate


def list_files(repo_root: str | Path, extensions: tuple[str, ...] | None = None) -> list[str]:
    """Return repo-relative file paths, skipping VCS/build/dependency dirs."""
    root = Path(repo_root).resolve()
    results: list[str] = []

    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if any(part in EXCLUDED_DIRS for part in path.relative_to(root).parts):
            continue
        if extensions and path.suffix not in extensions:
            continue
        results.append(str(path.relative_to(root)))

    return results


def read_file(repo_root: str | Path, relative_path: str, max_bytes: int = MAX_READ_BYTES) -> str:
    path = resolve_safe_path(repo_root, relative_path)
    if not path.is_file():
        raise FileNotFoundError(relative_path)

    data = path.read_bytes()[:max_bytes]
    return data.decode("utf-8", errors="replace")


def write_file(repo_root: str | Path, relative_path: str, content: str) -> None:
    path = resolve_safe_path(repo_root, relative_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


@dataclass
class TestResult:
    passed: bool
    command: str
    output: str


# Fixed allowlist of (marker file, command) pairs, checked in order. The
# command is never derived from model output or issue text — only from
# which of these marker files exist in the repo.
#
# `-B` disables writing/reading .pyc bytecode caches. Without it, an agent
# that rewrites a same-length file more than once within the same wall-clock
# second can hit a real CPython gotcha: pyc invalidation keys on (mtime
# truncated to whole seconds, file size), so two distinct fixes with the
# same byte length written in the same second are indistinguishable from
# "unchanged" and the *stale* cached bytecode silently runs instead.
#
# Every language in agent.SOURCE_EXTENSIONS should have at least one entry
# here; otherwise the agent could edit files it has no way to verify.
_TEST_RUNNERS: list[tuple[str, list[str]]] = [
    ("pytest.ini", ["python3", "-B", "-m", "pytest", "-q"]),
    ("pyproject.toml", ["python3", "-B", "-m", "pytest", "-q"]),
    ("package.json", ["npm", "test", "--silent"]),
    ("go.mod", ["go", "test", "./..."]),
    ("pom.xml", ["mvn", "-q", "test"]),
    ("build.gradle", ["gradle", "test", "-q"]),
    ("build.gradle.kts", ["gradle", "test", "-q"]),
    (".rspec", ["bundle", "exec", "rspec"]),
    ("Rakefile", ["bundle", "exec", "rake", "test"]),
]

SUPPORTED_MARKERS = tuple(marker for marker, _ in _TEST_RUNNERS)


def detect_test_command(repo_root: str | Path) -> list[str] | None:
    root = Path(repo_root)
    for marker, command in _TEST_RUNNERS:
        if (root / marker).is_file():
            return command
    return None


def run_tests(repo_root: str | Path, timeout: int = 120) -> TestResult:
    root = Path(repo_root).resolve()
    command = detect_test_command(root)

    if command is None:
        return TestResult(
            passed=False,
            command="<none>",
            output="No recognized test runner found (looked for "
                   f"{', '.join(SUPPORTED_MARKERS)}).",
        )

    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}

    try:
        proc = subprocess.run(
            command,
            cwd=root,
            capture_output=True,
            text=True,
            timeout=timeout,
            shell=False,
            env=env,
            check=False,  # a failing test run is an expected outcome, not an error
        )
    except subprocess.TimeoutExpired:
        return TestResult(
            passed=False,
            command=" ".join(command),
            output=f"Test run timed out after {timeout}s.",
        )
    except FileNotFoundError:
        # The marker file exists, but the tool itself (go, mvn, bundle, ...)
        # isn't installed on this machine.
        return TestResult(
            passed=False,
            command=" ".join(command),
            output=f"Test runner {command[0]!r} is not installed or not on PATH.",
        )

    output = (proc.stdout + proc.stderr)[-MAX_READ_BYTES:]
    return TestResult(passed=proc.returncode == 0, command=" ".join(command), output=output)
