"""
The fix loop: explore the repo -> plan+edit -> run tests -> retry with the
failure as feedback, up to max_iterations, or stop as soon as tests pass.

Opening a PR is deliberately *not* a node in this graph — it's a single,
one-shot action the CLI takes after the graph settles, based on whether the
final state has tests_passed. Keeping it out of the loop means the retry
logic can't accidentally trigger a real GitHub side effect partway through,
and it keeps this graph's job narrowly "converge on a working fix."
"""
from __future__ import annotations

from langgraph.graph import END, StateGraph

from src.llm import Planner
from src.state import AgentState
from src.tools import repo_tools

SOURCE_EXTENSIONS = (".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".java", ".rb")
MAX_FILES_IN_CONTEXT = 12


def build_graph(planner: Planner):
    def explore_and_plan(state: AgentState) -> AgentState:
        state["iteration"] += 1

        files = repo_tools.list_files(state["repo_path"], extensions=SOURCE_EXTENSIONS)
        selected = files[:MAX_FILES_IN_CONTEXT]
        file_contents = {f: repo_tools.read_file(state["repo_path"], f) for f in selected}

        feedback = state["test_output"] if state["iteration"] > 1 else None
        response = planner(
            issue_title=state["issue_title"],
            issue_body=state["issue_body"],
            file_contents=file_contents,
            feedback=feedback,
        )

        for edit in response.edits:
            repo_tools.write_file(state["repo_path"], edit.path, edit.content)

        state["plan"] = response.plan
        state["edits"] = [e.model_dump() for e in response.edits]
        state["log"].append(f"Iteration {state['iteration']}: {response.plan}")
        return state

    def run_tests_node(state: AgentState) -> AgentState:
        result = repo_tools.run_tests(state["repo_path"])
        state["tests_passed"] = result.passed
        state["test_output"] = result.output
        outcome = "passed" if result.passed else "failed"
        state["log"].append(f"Iteration {state['iteration']}: tests {outcome} ({result.command})")
        return state

    def route(state: AgentState) -> str:
        if state["tests_passed"]:
            return "done"
        if state["iteration"] >= state["max_iterations"]:
            return "done"
        return "retry"

    graph = StateGraph(AgentState)
    graph.add_node("explore_and_plan", explore_and_plan)
    graph.add_node("run_tests", run_tests_node)
    graph.set_entry_point("explore_and_plan")
    graph.add_edge("explore_and_plan", "run_tests")
    graph.add_conditional_edges("run_tests", route, {"retry": "explore_and_plan", "done": END})

    return graph.compile()
