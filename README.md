# issue-to-pr-agent

[![CI](https://github.com/limeizhou2026/issue-to-pr-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/limeizhou2026/issue-to-pr-agent/actions/workflows/ci.yml)

A coding agent that takes a GitHub issue, explores a repo, proposes and
applies a fix, runs the test suite, and iterates on its own test failures —
retrying with the failure as feedback — until the tests pass or it gives up.
Only then, and only if you ask it to, does it open a pull request.

This is deliberately a different kind of project than
[aws-ai-assistant](https://github.com/limeizhou2026/aws-ai-assistant): that
one is a single prompt-in, response-out call to Claude behind a serverless
pipeline. This one is a real **agent** — a loop with a plan → act → verify →
retry cycle, tool use, and bounded autonomy, built on LangGraph.

## How It Works

```
Issue (title + body)
        |
        v
+-------------------+
|  explore_and_plan  |<--------------------+
|  read relevant     |                     |
|  files, call the   |                     | retry, with the
|  LLM for a fix      |                    | last failure as
+-------------------+                      | feedback
        |                                  |
        v                                  |
+-------------------+     tests failed     |
|     run_tests      |----------------------+
|  fixed allowlist   |
|  of test commands   |
+-------------------+
        |
        | tests passed, OR iteration == max_iterations
        v
+-------------------+
|   open_pull_request |  dry run by default — prints the diff.
|   (outside the loop) |  --allow-pr actually pushes, and only
+-------------------+  if the fix's own tests passed.
```

1. **Explore** — list the repo's source files, read the ones most likely
   relevant (bounded by count and size so a large repo doesn't blow the
   context window).
2. **Plan + edit** — Claude proposes a fix as full file contents (not a
   diff) for the files it wants to change; those are written to disk inside
   the sandbox.
3. **Test** — run the repo's own test suite via a command chosen from a
   fixed allowlist based on which project files exist (`pytest.ini` /
   `pyproject.toml` → pytest, `package.json` → `npm test`) — never a command
   built from LLM or issue text.
4. **Retry or stop** — if tests fail and iterations remain, go back to step
   2 with the failure output as feedback. Otherwise stop.
5. **Open a PR** — a single action taken once, outside the retry loop, after
   the graph settles. Dry run by default: it computes and prints the real
   git diff but never pushes or calls the GitHub API. `--allow-pr` pushes a
   branch and opens a real PR — and only runs at all if the fix's own tests
   passed.

## Why This Design (and not something simpler)

**The LLM call is behind a swappable interface (`src/llm.py`), so the
retry loop is unit-tested without needing a real API key or a paid call on
every test run.** `FakePlanner` returns a scripted sequence of responses;
the tests in `tests/test_agent.py` prove the graph actually retries with
the previous failure as feedback, and actually stops at `max_iterations`
instead of looping forever — the same testing philosophy as
`aws-ai-assistant`'s Bedrock-mocking tests, applied to a stateful loop
instead of a single call.

**File access is sandboxed, and the test command is never LLM-controlled.**
An issue body is untrusted input — anyone can file a GitHub issue, and its
text ends up in the model's context. If that text could shape a shell
command or an unchecked file path, a malicious issue could walk the agent
into reading `~/.ssh/id_rsa` or executing arbitrary code. Two things close
that off: every path is resolved and verified to stay inside the repo root
before any read/write (`resolve_safe_path` in `src/tools/repo_tools.py`,
tested against `../` traversal, absolute-path escapes, *and* a symlink that
resolves outside the sandbox), and the test runner only ever executes one
of a fixed set of commands chosen by which marker file exists in the repo —
never a string built from model output.

**Opening a PR is a one-shot action outside the graph, not a node in the
loop.** Keeping it out of the retry cycle means the agent can't accidentally
trigger a real GitHub side effect partway through an attempt, and it means
the dry-run/`--allow-pr` gate only has to be checked in one place.

## A Real Bug This Project's Tests Caught

While testing the retry loop, a second (correct) fix attempt kept failing
with the *same* error as the first, wrong attempt — even though the file on
disk had the right content. The cause: CPython's default `.pyc` bytecode
cache invalidates on `(mtime truncated to whole seconds, file size)`. Two
fix attempts of the same byte length, written within the same wall-clock
second, are indistinguishable from "unchanged" under that scheme, so the
*second* test run silently executed *stale* cached bytecode from the first,
wrong attempt. Any fast-iterating agent that rewrites files repeatedly can
hit this. Fixed by disabling bytecode caching for the test subprocess
(`-B` / `PYTHONDONTWRITEBYTECODE=1` in `src/tools/repo_tools.py`), with a
regression test (`test_rapid_same_size_rewrites_are_not_served_stale_bytecode`)
that fails without the fix.

## Try It Yourself (No GitHub Token Needed)

`examples/demo_repo` has a small, intentionally broken repo — a discount
calculator that treats `percent` as a raw multiplier instead of dividing by
100 — plus `issue.txt` describing the bug the way a real GitHub issue would.

```bash
cp -r examples/demo_repo /tmp/demo && cd /tmp/demo && git init -q \
  && git add -A && git -c user.email=you@example.com -c user.name=You commit -q -m init && cd -

python -m src.cli --repo /tmp/demo --issue-file examples/demo_repo/issue.txt
```

Without `--allow-pr`, this prints the agent's reasoning log and a diff —
nothing is pushed. Requires `ANTHROPIC_API_KEY` in `.env` (copy
`.env.example`) since this run makes real Claude calls; the automated test
suite covers the same flow with `FakePlanner` so `pytest` needs no API key
at all.

## Tech Stack

- **Agent orchestration:** LangGraph (a `StateGraph` with a conditional
  retry edge)
- **LLM:** Claude Sonnet 4.5 via `langchain-anthropic`, with structured
  output (a Pydantic schema) for the plan/edits response
- **GitHub integration:** PyGithub (issues, PRs) + GitPython (branch,
  commit, push)
- **Language:** Python 3.11

## Known Limitations

- **File selection is a heuristic, not retrieval.** `explore_and_plan`
  reads up to 12 source files by simple extension filtering, not by
  relevance to the issue. On a large repo this could miss the actually
  relevant file. A production version would use embeddings or a
  code-search tool to select files instead.
- **One flat retry loop, no sub-planning.** Every retry re-plans from
  scratch with the last failure as feedback; there's no explicit "which
  file is wrong" diagnosis step. Works for small, localized bugs; a more
  sophisticated agent would isolate the failing assertion before deciding
  what to change.
- **No cost/token budget beyond `max_iterations`.** Each iteration reads up
  to 12 files into context; nothing currently caps total tokens spent
  across a run beyond capping the iteration count.

## Testing & CI

```bash
pip install -r requirements-dev.txt
ruff check .
pytest -v
```

34 tests, all deterministic (no real API key or network access required):
sandbox-escape attempts (`tests/test_repo_tools.py`), the retry/feedback/
give-up control flow (`tests/test_agent.py`), the dry-run/real-push branches
of the GitHub tools including a real push to a local bare repo
(`tests/test_github_tools.py`), CLI argument handling (`tests/test_cli.py`),
and the full pipeline against the example repo
(`tests/test_end_to_end.py`). [ci.yml](.github/workflows/ci.yml) runs all
of it, plus `ruff check`, on every push and PR.

## Project Structure

```
src/
  agent.py           LangGraph StateGraph: explore -> plan -> edit -> test -> retry/stop
  llm.py             Planner interface: ClaudePlanner (real) and FakePlanner (for tests)
  state.py           AgentState TypedDict
  cli.py             Entry point — argument parsing, wiring it all together
  tools/
    repo_tools.py    Sandboxed file I/O + the test runner
    github_tools.py  fetch_issue, open_pull_request (dry-run gated)
examples/demo_repo/  A tiny broken repo + issue.txt, for running the agent without a GitHub token
tests/               34 tests covering all of the above
```
