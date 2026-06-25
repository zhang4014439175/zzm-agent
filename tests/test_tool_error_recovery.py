import json
from unittest.mock import MagicMock

from zzm_agent.core.agent_loop import AgentLoop
from zzm_agent.core.tool_registry import ToolRegistry

from tests.test_agent_loop import make_response


class FakeStore:
    def __init__(self):
        self.history: list[dict] = []
        self.latest_context: dict | None = None

    def load_history(self) -> list[dict]:
        return list(self.history)

    def build_turn_messages(
        self,
        *,
        system_prompt: str,
        user_input: str,
        memory_limit: int,
    ) -> tuple[list[dict], dict]:
        return [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_input},
        ], {}

    def append(self, messages: list[dict]) -> None:
        self.history.extend(messages)

    def save_latest_context(self, payload: dict) -> None:
        self.latest_context = payload


def _tool_call(name: str, arguments: dict):
    tool_call = MagicMock()
    tool_call.id = "call_1"
    tool_call.function.name = name
    tool_call.function.arguments = json.dumps(arguments)
    return tool_call


def _loop(registry: ToolRegistry, *, sleeps=None, max_tool_retries: int = 2):
    store = FakeStore()
    loop = AgentLoop(
        client=MagicMock(),
        model="test-model",
        system_prompt="sys",
        registry=registry,
        store=store,
        max_tool_retries=max_tool_retries,
        retry_base_delay=0.5,
        retry_sleep=sleeps.append if sleeps is not None else None,
    )
    return loop, store


def test_deterministic_argument_error_is_not_automatically_retried():
    registry = ToolRegistry()
    calls = {"count": 0}

    @registry.tool(description="requires value")
    def requires_value(value: str) -> str:
        calls["count"] += 1
        return value

    loop, store = _loop(registry, max_tool_retries=3)
    loop.client.chat.completions.create.side_effect = [
        make_response(tool_calls=[_tool_call("requires_value", {})]),
        make_response(content="handled"),
    ]

    result = loop.run("call with missing arg", stream=False)

    assert result == "handled"
    assert calls["count"] == 0
    payload = json.loads(store.load_history()[2]["content"])
    assert payload["category"] == "argument"
    assert payload["deterministic"] is True
    assert payload["retryable"] is False
    assert payload["attempts"] == 1
    assert "Check required parameters" in payload["recovery_hint"]


def test_retryable_timeout_uses_exponential_backoff_before_success():
    registry = ToolRegistry()
    calls = {"count": 0}
    sleeps: list[float] = []

    @registry.tool(description="flaky timeout")
    def flaky_timeout() -> str:
        calls["count"] += 1
        if calls["count"] < 3:
            raise TimeoutError("temporary timeout")
        return "ok"

    loop, store = _loop(registry, sleeps=sleeps, max_tool_retries=3)
    loop.client.chat.completions.create.side_effect = [
        make_response(tool_calls=[_tool_call("flaky_timeout", {})]),
        make_response(content="handled"),
    ]

    result = loop.run("retry flaky", stream=False)

    assert result == "handled"
    assert calls["count"] == 3
    assert sleeps == [0.5, 1.0]
    assert store.load_history()[2]["content"] == "ok"


def test_external_service_error_respects_retry_after_and_reports_recovery_summary():
    registry = ToolRegistry()
    calls = {"count": 0}
    sleeps: list[float] = []

    @registry.tool(description="external service")
    def external_service() -> str:
        calls["count"] += 1
        exc = ConnectionError("rate limited")
        exc.retry_after_seconds = 2.0
        raise exc

    loop, store = _loop(registry, sleeps=sleeps, max_tool_retries=1)
    loop.client.chat.completions.create.side_effect = [
        make_response(tool_calls=[_tool_call("external_service", {})]),
        make_response(content="handled"),
    ]

    result = loop.run("call external", stream=False)

    assert result == "handled"
    assert calls["count"] == 2
    assert sleeps == [2.0]
    payload = json.loads(store.load_history()[2]["content"])
    assert payload["category"] == "external_service"
    assert payload["retry_after_seconds"] == 2.0
    assert payload["attempts"] == 2
    assert payload["retryable"] is True
    assert "Automatic retry exhausted after 1 retry attempt(s)." in payload["recovery_hint"]
