from typing import TypedDict


class AgentState(TypedDict):
    repo_path: str
    issue_title: str
    issue_body: str
    iteration: int
    max_iterations: int
    plan: str
    edits: list[dict]
    test_output: str
    tests_passed: bool
    log: list[str]


def initial_state(
    *, repo_path: str, issue_title: str, issue_body: str, max_iterations: int = 3
) -> AgentState:
    return AgentState(
        repo_path=repo_path,
        issue_title=issue_title,
        issue_body=issue_body,
        iteration=0,
        max_iterations=max_iterations,
        plan="",
        edits=[],
        test_output="",
        tests_passed=False,
        log=[],
    )
