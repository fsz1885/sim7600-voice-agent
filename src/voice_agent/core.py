import asyncio

from pydantic import ValidationError

from .models import Decision, Evidence, FieldState, Message, Proposal, State, Task
from .providers import Provider, ProviderError


class Agent:
    """One caller per State. Model proposals are validated before atomic state updates."""

    def __init__(
        self,
        provider: Provider,
        *,
        timeout: float = 30,
        max_turns: int = 40,
        max_stalled_turns: int = 4,
    ):
        if timeout <= 0 or max_turns < 1 or max_stalled_turns < 1:
            raise ValueError("Limits must be positive")
        self.provider = provider
        self.timeout = timeout
        self.max_turns = max_turns
        self.max_stalled_turns = max_stalled_turns

    async def handle_turn(self, *, task: Task, state: State, user_text: str | None) -> Decision:
        if task != state.task or set(state.fields) != set(task.required_fields):
            raise ValueError("State belongs to a different task")
        if state.status != "active":
            if state.current_decision is None:
                raise ValueError("Terminal state has no decision")
            return state.current_decision.model_copy(deep=True)
        if user_text is None and state.history:
            raise ValueError("None is only allowed for the opening turn")
        if user_text is not None:
            state.history.append(Message(role="user", content=user_text))
            state.turns += 1

        before = {n: (f.status, f.value) for n, f in state.fields.items()}
        error = None
        decision = None
        # One repair attempt; no silent partial commits on malformed model output.
        for _ in range(2):
            try:
                raw = await asyncio.wait_for(
                    self.provider.propose(
                        task.model_copy(deep=True), state.model_copy(deep=True), error
                    ),
                    timeout=self.timeout,
                )
                proposal = Proposal.model_validate_json(raw)
                decision, fields = self._validate(proposal, state, user_text)
                state.fields = fields
                break
            except (ValidationError, ValueError, ProviderError, TimeoutError):
                # Avoid leaking provider responses, credentials or raw conversation in diagnostics.
                error = (
                    "Previous proposal was invalid or unavailable. Return the exact schema; "
                    "use only task fields and exact quotes from the latest user message; "
                    "ask one unconfirmed field, clarify partial fields, finish only when complete."
                )
        if decision is None:
            decision = Decision(
                action="retry",
                response="抱歉，刚才的信息暂时没能处理好，请您再说一次。",
                reason="Provider failed or proposal did not pass validation; fields preserved",
            )
        if user_text is not None:
            after = {n: (f.status, f.value) for n, f in state.fields.items()}
            state.stalled_turns = state.stalled_turns + 1 if before == after else 0
        if decision.action == "finish":
            state.status = "completed"
        elif state.turns >= self.max_turns or state.stalled_turns >= self.max_stalled_turns:
            state.status = "handoff"
            decision = Decision(
                action="handoff",
                updates=decision.updates,
                response="还有部分信息没有确认，我们先到这里，后续再联系，谢谢。",
                reason="Turn or no-progress limit reached; task remains incomplete",
            )
        state.current_decision = decision.model_copy(deep=True)
        state.history.append(Message(role="assistant", content=decision.response))
        if state.status != "active":
            state.final_result = state.result()
        return decision

    @staticmethod
    def _validate(p: Proposal, state: State, user_text: str | None):
        fields = {n: f.model_copy(deep=True) for n, f in state.fields.items()}
        updates = {}
        if not p.response.strip() or len(p.response) > 400 or not p.reason.strip():
            raise ValueError("Empty or overlong decision")
        for u in p.updates:
            if u.field not in fields or u.field in updates:
                raise ValueError("Unknown or duplicate field")
            if not u.value.strip() or not u.evidence.strip():
                raise ValueError("Empty value or evidence")
            if user_text is None or u.evidence not in user_text:
                raise ValueError("Evidence must quote the current user message")
            previous = fields[u.field]
            fields[u.field] = FieldState(
                status=u.status,
                value=u.value,
                evidence=[*previous.evidence, Evidence(turn=state.turns, quote=u.evidence)],
            )
            updates[u.field] = fields[u.field].model_copy(deep=True)
        complete = all(f.status == "confirmed" for f in fields.values())
        if complete:
            return Decision(
                action="finish",
                response="好的，相关信息已经了解清楚了，谢谢您，再见。",
                updates=updates,
                reason="All required fields are confirmed",
            ), fields
        if p.action == "finish":
            raise ValueError("Premature completion")
        if p.target_field not in fields or fields[p.target_field].status == "confirmed":
            raise ValueError("Question must target an unconfirmed field")
        if fields[p.target_field].status == "partial" and p.action != "clarify":
            raise ValueError("Partial information requires clarification")
        return Decision(**p.model_dump(exclude={"updates"}), updates=updates), fields
