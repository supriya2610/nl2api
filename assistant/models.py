from typing import Any, Literal
from pydantic import BaseModel, Field


class PlanStep(BaseModel):
    id: str
    operation_id: str
    args: dict[str, Any] = Field(default_factory=dict)  # values may be refs like "$s1.items.0.id"
    reason: str = ""
    expected: str = ""
    expect_single: bool = False  # lookup steps: 0 or >1 results => ask, never guess


class Plan(BaseModel):
    steps: list[PlanStep] = Field(default_factory=list)
    clarification: str | None = None
    refusal: str | None = None


Status = Literal["planned", "running", "awaiting_approval", "needs_clarification",
                 "rejected", "completed", "failed", "cancelled"]


class RunState(BaseModel):
    """Typed workflow state: everything needed to pause and resume a run."""
    run_id: str
    request: str
    role: str
    plan: Plan
    status: Status = "planned"
    cursor: int = 0
    results: dict[str, Any] = Field(default_factory=dict)
    approved_steps: list[str] = Field(default_factory=list)
    pending_preview: dict[str, Any] | None = None
    candidates: list[dict[str, Any]] | None = None
    message: str | None = None
    answer: str | None = None
