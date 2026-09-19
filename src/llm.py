"""
The planning step, behind a small interface so the graph's control flow
(src/agent.py) can be unit-tested with a scripted fake planner instead of
paying for — and depending on the availability of — a real Claude call on
every test run. ClaudePlanner is the real implementation; FakePlanner below
returns a pre-programmed sequence of responses for tests.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from pydantic import BaseModel, Field


class FileEdit(BaseModel):
    path: str = Field(description="Repo-relative path to create or overwrite.")
    content: str = Field(description="The full new content of the file.")


class PlanResponse(BaseModel):
    plan: str = Field(description="One or two sentences: what you're changing and why.")
    edits: list[FileEdit] = Field(description="Files to create or overwrite to implement the plan.")


class Planner(Protocol):
    def __call__(
        self,
        *,
        issue_title: str,
        issue_body: str,
        file_contents: dict[str, str],
        feedback: str | None,
    ) -> PlanResponse: ...


SYSTEM_PROMPT = """You are a software engineer fixing a GitHub issue in an \
existing repository. You will be shown the issue and the current contents \
of the files most likely relevant to it.

Propose the smallest change that plausibly fixes the issue. Return the full \
new content for every file you touch — not a diff. If a previous attempt's \
test output is provided, use it to correct course rather than repeating the \
same change."""


class ClaudePlanner:
    """Calls Claude via langchain_anthropic, with structured output."""

    def __init__(self, model: str = "claude-sonnet-4-5-20250929", temperature: float = 0.0):
        from langchain_anthropic import ChatAnthropic

        self._llm = ChatAnthropic(model=model, temperature=temperature).with_structured_output(
            PlanResponse
        )

    def __call__(
        self,
        *,
        issue_title: str,
        issue_body: str,
        file_contents: dict[str, str],
        feedback: str | None,
    ) -> PlanResponse:
        files_block = "\n\n".join(
            f"--- {path} ---\n{content}" for path, content in file_contents.items()
        )
        feedback_block = (
            f"\n\nThe previous attempt's test output was:\n{feedback}" if feedback else ""
        )
        user_message = (
            f"Issue: {issue_title}\n\n{issue_body}\n\n"
            f"Relevant files:\n{files_block}{feedback_block}"
        )

        return self._llm.invoke(
            [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user_message}]
        )


@dataclass
class FakePlanner:
    """Returns a scripted sequence of responses, one per call. For tests."""

    responses: list[PlanResponse]
    calls: list[dict] = field(default_factory=list)

    def __call__(
        self,
        *,
        issue_title: str,
        issue_body: str,
        file_contents: dict[str, str],
        feedback: str | None,
    ) -> PlanResponse:
        self.calls.append(
            {
                "issue_title": issue_title,
                "file_contents": file_contents,
                "feedback": feedback,
            }
        )
        return self.responses[len(self.calls) - 1]
