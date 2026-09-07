from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Task(Model):
    goal: str = Field(min_length=1)
    required_fields: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def valid_fields(self):
        if not self.goal.strip() or any(not f.strip() for f in self.required_fields):
            raise ValueError("Goal and field names must not be blank")
        if len(set(self.required_fields)) != len(self.required_fields):
            raise ValueError("Duplicate required fields")
        return self


class Evidence(Model):
    turn: int
    quote: str


class FieldState(Model):
    status: Literal["unknown", "partial", "confirmed"] = "unknown"
    value: str | None = None
    evidence: list[Evidence] = Field(default_factory=list)


class Message(Model):
    role: Literal["user", "assistant"]
    content: str


class Update(Model):
    field: str
    status: Literal["partial", "confirmed"]
    value: str
    evidence: str


class Proposal(Model):
    """The provider contract, also used as its JSON output schema."""

    response: str
    action: Literal["continue", "clarify", "finish"]
    target_field: str | None
    updates: list[Update]
    reason: str


class Decision(Model):
    response: str
    action: Literal["continue", "clarify", "finish", "retry", "handoff"]
    target_field: str | None = None
    updates: dict[str, FieldState] = Field(default_factory=dict)
    reason: str


class State(Model):
    task: Task
    history: list[Message] = Field(default_factory=list)
    fields: dict[str, FieldState]
    status: Literal["active", "completed", "handoff"] = "active"
    current_decision: Decision | None = None
    final_result: dict | None = None
    turns: int = 0
    stalled_turns: int = 0

    @classmethod
    def for_task(cls, task: Task):
        return cls(
            task=task.model_copy(deep=True),
            fields={name: FieldState() for name in task.required_fields},
        )

    def result(self) -> dict:
        return {
            "completed": self.status == "completed",
            "status": self.status,
            "goal": self.task.goal,
            "fields": {name: f.value for name, f in self.fields.items()},
            "field_details": {name: f.model_dump() for name, f in self.fields.items()},
            "missing_fields": [n for n, f in self.fields.items() if f.status != "confirmed"],
        }
