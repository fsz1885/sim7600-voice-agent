import asyncio
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from voice_agent import Agent, State, Task
from voice_agent.models import Proposal, Update
from voice_agent.providers import MockProvider, ProviderError


def run(agent, state, text=None):
    return asyncio.run(agent.handle_turn(task=state.task, state=state, user_text=text))


def setup(fields=("费用", "材料"), **limits):
    task = Task(goal="确认办理条件", required_fields=list(fields))
    return Agent(MockProvider(), **limits), State.for_task(task)


class ScriptedProvider:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = 0

    async def propose(self, task, state, error=None):
        index = min(self.calls, len(self.responses) - 1)
        self.calls += 1
        item = self.responses[index]
        if isinstance(item, Exception):
            raise item
        return item


def proposal(target="材料", action="continue", updates=()):
    return Proposal(
        response=f"请问{target}？",
        action=action,
        target_field=target,
        updates=list(updates),
        reason="获取缺失信息",
    ).model_dump_json()


def update(field="费用", value="免费", status="confirmed", evidence="免费"):
    return Update(field=field, value=value, status=status, evidence=evidence)


def test_initialization_and_opening():
    agent, state = setup()
    assert not state.history and state.final_result is None
    assert all(f.status == "unknown" for f in state.fields.values())
    assert run(agent, state).target_field == "费用"
    assert state.history[0].role == "assistant"
    assert state.turns == 0


@pytest.mark.parametrize("fields", [[], ["a", "a"], [" "]])
def test_invalid_task(fields):
    with pytest.raises(ValidationError):
        Task(goal="g", required_fields=fields)


def test_unknown_partial_confirmed_and_evidence():
    agent, state = setup()
    run(agent, state)
    d = run(agent, state, "大概一千")
    assert d.action == "clarify"
    assert state.fields["费用"].status == "partial"
    d = run(agent, state, "每年1000元")
    assert d.target_field == "材料"
    assert state.fields["费用"].status == "confirmed"
    assert [e.quote for e in state.fields["费用"].evidence] == ["大概一千", "每年1000元"]
    assert state.fields["费用"].evidence[-1].turn == 2


def test_multi_field_update_skips_answered_question():
    agent, state = setup(("费用", "材料", "周期"))
    run(agent, state)
    d = run(agent, state, "费用=免费;材料=身份证")
    assert d.target_field == "周期"
    assert set(d.updates) == {"费用", "材料"}


def test_completion_and_terminal_idempotence():
    agent, state = setup()
    run(agent, state)
    d = run(agent, state, "费用=免费;材料=身份证")
    assert d.action == "finish"
    assert state.final_result["completed"] is True
    snapshot = state.model_dump()
    assert run(agent, state, "more") == d
    assert state.model_dump() == snapshot


def test_full_company_dialogue():
    path = Path("examples/company-registration.json")
    task = Task.model_validate_json(path.read_text(encoding="utf-8"))
    rows = json.loads(path.with_suffix(".dialogue.json").read_text(encoding="utf-8"))
    agent = Agent(MockProvider({r["user"]: r["updates"] for r in rows}))
    state = State.for_task(task)
    run(agent, state)
    decisions = [run(agent, state, row["user"]) for row in rows]
    assert any(d.action == "clarify" for d in decisions)
    assert decisions[-1].action == "finish"
    assert state.final_result["fields"]["代理记账价格"] == "2400元/年"
    assert len(state.final_result["fields"]) == 9
    assert state.final_result["missing_fields"] == []


@pytest.mark.parametrize(
    "raw",
    [
        "not json",
        '{"response":"hi"}',
        proposal(updates=[update(field="invalid")]),
        proposal(updates=[update(evidence="fabricated")]),
        proposal(updates=[update(), update()]),
        proposal(action="finish", target=None),
        proposal(target="费用", updates=[update()]),
        proposal(target="费用", updates=[update(status="partial")]),
        proposal(updates=[update(value=" ")]),
        proposal(updates=[update(evidence="")]),
        proposal(updates=[update(), update(field="invalid")]),
        ProviderError("unavailable"),
    ],
)
def test_invalid_output_and_provider_errors_preserve_fields(raw):
    _, state = setup()
    provider = ScriptedProvider(raw)
    d = run(Agent(provider), state, "免费")
    assert d.action == "retry"
    assert provider.calls == 2
    assert all(f.status == "unknown" and not f.evidence for f in state.fields.values())
    assert state.history[0].content == "免费"


def test_repair_attempt_commits_once():
    _, state = setup()
    provider = ScriptedProvider("bad", proposal(updates=[update()]))
    d = run(Agent(provider), state, "免费")
    assert d.action == "continue"
    assert len(state.fields["费用"].evidence) == 1


def test_cannot_confirm_from_assistant_opening():
    _, state = setup()
    assert run(Agent(ScriptedProvider(proposal(updates=[update()]))), state).action == "retry"


def test_state_task_mismatch():
    agent, state = setup()
    with pytest.raises(ValueError, match="different task"):
        asyncio.run(
            agent.handle_turn(
                task=Task(goal="other", required_fields=["费用"]),
                state=state,
                user_text="hi",
            )
        )
    assert not state.history


def test_correction_and_conflict_can_reopen_field():
    agent, state = setup()
    run(agent, state, "费用=免费")
    d = run(agent, state, "费用=?可能收费")
    assert d.action == "clarify"
    assert state.fields["费用"].status == "partial"
    run(agent, state, "费用=每年1000元")
    assert state.fields["费用"].value == "每年1000元"
    assert len(state.fields["费用"].evidence) == 3


def test_no_progress_handoff_and_incomplete_result():
    _, state = setup()
    agent = Agent(ScriptedProvider("bad"), max_stalled_turns=2)
    run(agent, state, "不知道")
    assert run(agent, state, "不知道").action == "handoff"
    assert not state.final_result["completed"]
    assert state.final_result["missing_fields"] == ["费用", "材料"]


def test_turn_limit_does_not_override_success():
    agent, state = setup(("费用",), max_turns=1)
    assert run(agent, state, "费用=免费").action == "finish"


def test_turn_limit_handoff():
    agent, state = setup(max_turns=1)
    assert run(agent, state, "费用=免费").action == "handoff"


def test_provider_cannot_mutate_original_state():
    class MutatingProvider:
        async def propose(self, task, state, error=None):
            state.fields.clear()
            task.required_fields.clear()
            raise ProviderError("failed")

    _, state = setup()
    run(Agent(MutatingProvider()), state, "hi")
    assert len(state.fields) == len(state.task.required_fields) == 2


def test_timeout_is_bounded():
    class SlowProvider:
        async def propose(self, task, state, error=None):
            await asyncio.sleep(10)

    _, state = setup()
    assert run(Agent(SlowProvider(), timeout=0.001), state, "hi").action == "retry"


def test_decision_mutation_does_not_change_state():
    agent, state = setup()
    d = run(agent, state, "费用=免费")
    d.updates["费用"].value = "corrupted"
    assert state.fields["费用"].value == "免费"
    assert state.current_decision.updates["费用"].value == "免费"


def test_rehydrated_state_continues():
    agent, state = setup()
    run(agent, state, "费用=免费")
    restored = State.model_validate_json(state.model_dump_json())
    assert run(agent, restored, "材料=身份证").action == "finish"
