from src.agent import build_graph
from src.llm import FakePlanner, FileEdit, PlanResponse
from src.state import initial_state


def _buggy_repo(tmp_path):
    (tmp_path / "pytest.ini").write_text("[pytest]\n")
    (tmp_path / "buggy.py").write_text("def add(a, b):\n    return a - b\n")
    (tmp_path / "test_buggy.py").write_text(
        "from buggy import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n"
    )
    return tmp_path


def test_stops_as_soon_as_the_first_attempt_passes(tmp_path):
    repo = _buggy_repo(tmp_path)
    planner = FakePlanner(
        responses=[
            PlanResponse(
                plan="Fix the operator in add().",
                edits=[FileEdit(path="buggy.py", content="def add(a, b):\n    return a + b\n")],
            )
        ]
    )

    graph = build_graph(planner)
    result = graph.invoke(
        initial_state(
            repo_path=str(repo), issue_title="add() is wrong", issue_body="2+3 should be 5", max_iterations=3
        )
    )

    assert result["tests_passed"] is True
    assert result["iteration"] == 1
    assert len(planner.calls) == 1


def test_retries_with_failure_feedback_and_then_succeeds(tmp_path):
    repo = _buggy_repo(tmp_path)
    planner = FakePlanner(
        responses=[
            # First attempt: a plausible-looking but still-wrong fix.
            PlanResponse(
                plan="Try swapping operands.",
                edits=[FileEdit(path="buggy.py", content="def add(a, b):\n    return b - a\n")],
            ),
            # Second attempt: the real fix.
            PlanResponse(
                plan="Actually fix the operator.",
                edits=[FileEdit(path="buggy.py", content="def add(a, b):\n    return a + b\n")],
            ),
        ]
    )

    graph = build_graph(planner)
    result = graph.invoke(
        initial_state(
            repo_path=str(repo), issue_title="add() is wrong", issue_body="2+3 should be 5", max_iterations=3
        )
    )

    assert result["tests_passed"] is True
    assert result["iteration"] == 2
    assert len(planner.calls) == 2
    # The second call must have seen the first attempt's failure as feedback.
    assert planner.calls[1]["feedback"] is not None
    assert "1 failed" in planner.calls[1]["feedback"]


def test_gives_up_after_max_iterations_without_looping_forever(tmp_path):
    repo = _buggy_repo(tmp_path)
    # Every attempt is wrong on purpose.
    bad_response = PlanResponse(
        plan="A fix that never works.",
        edits=[FileEdit(path="buggy.py", content="def add(a, b):\n    return a - b\n")],
    )
    planner = FakePlanner(responses=[bad_response, bad_response, bad_response])

    graph = build_graph(planner)
    result = graph.invoke(
        initial_state(
            repo_path=str(repo), issue_title="add() is wrong", issue_body="2+3 should be 5", max_iterations=3
        )
    )

    assert result["tests_passed"] is False
    assert result["iteration"] == 3
    assert len(planner.calls) == 3


def test_log_records_every_iteration(tmp_path):
    repo = _buggy_repo(tmp_path)
    planner = FakePlanner(
        responses=[
            PlanResponse(
                plan="Fix it.",
                edits=[FileEdit(path="buggy.py", content="def add(a, b):\n    return a + b\n")],
            )
        ]
    )

    graph = build_graph(planner)
    result = graph.invoke(
        initial_state(repo_path=str(repo), issue_title="t", issue_body="b", max_iterations=3)
    )

    assert any("Iteration 1: Fix it." in line for line in result["log"])
    assert any("tests passed" in line for line in result["log"])
