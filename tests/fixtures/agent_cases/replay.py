import json
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

from zzm_agent.core.tool_registry import ToolRegistry


@dataclass
class ReplayToolCall:
    """One deterministic tool call requested by a replayed model turn."""

    name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    call_id: str | None = None

    def normalized_id(self, index: int) -> str:
        return self.call_id or f"call_{index + 1}"

    def arguments_json(self) -> str:
        return json.dumps(self.arguments, ensure_ascii=False, sort_keys=True)

    def to_response_tool_call(self, index: int) -> SimpleNamespace:
        return SimpleNamespace(
            id=self.normalized_id(index),
            function=SimpleNamespace(
                name=self.name,
                arguments=self.arguments_json(),
            ),
        )

    def to_stream_delta(self, index: int) -> SimpleNamespace:
        return SimpleNamespace(
            index=index,
            id=self.normalized_id(index),
            function=SimpleNamespace(
                name=self.name,
                arguments=self.arguments_json(),
            ),
        )


@dataclass
class ReplayTurn:
    """One model response in a replay sequence."""

    content: str = ""
    tool_calls: list[ReplayToolCall | dict[str, Any]] = field(default_factory=list)

    def normalized_tool_calls(self) -> list[ReplayToolCall]:
        calls = []
        for index, call in enumerate(self.tool_calls):
            if isinstance(call, ReplayToolCall):
                calls.append(call)
                continue
            calls.append(
                ReplayToolCall(
                    name=str(call["name"]),
                    arguments=dict(call.get("arguments", {})),
                    call_id=call.get("call_id") or call.get("id") or f"call_{index + 1}",
                )
            )
        return calls

    def to_response(self) -> SimpleNamespace:
        message = SimpleNamespace(
            content=self.content,
            tool_calls=[
                call.to_response_tool_call(index)
                for index, call in enumerate(self.normalized_tool_calls())
            ],
            role="assistant",
        )
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])

    def to_stream(self):
        calls = self.normalized_tool_calls()
        if calls:
            yield SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        delta=SimpleNamespace(
                            content=self.content or None,
                            tool_calls=[
                                call.to_stream_delta(index)
                                for index, call in enumerate(calls)
                            ],
                        )
                    )
                ]
            )
            return

        if not self.content:
            yield SimpleNamespace(
                choices=[SimpleNamespace(delta=SimpleNamespace(content="", tool_calls=[]))]
            )
            return

        midpoint = max(1, len(self.content) // 2)
        for chunk in (self.content[:midpoint], self.content[midpoint:]):
            if chunk:
                yield SimpleNamespace(
                    choices=[
                        SimpleNamespace(
                            delta=SimpleNamespace(content=chunk, tool_calls=[])
                        )
                    ]
                )


class ReplayLLM:
    """OpenAI-compatible client that replays fixed chat completion turns."""

    def __init__(self, turns: list[ReplayTurn]):
        self.turns = list(turns)
        self.requests: list[dict[str, Any]] = []
        self._cursor = 0
        self.chat = SimpleNamespace(
            completions=SimpleNamespace(create=self.create)
        )

    @property
    def call_count(self) -> int:
        return len(self.requests)

    def create(self, **kwargs):
        self.requests.append(kwargs)
        if self._cursor >= len(self.turns):
            turn = ReplayTurn(content="(no more replay turns)")
        else:
            turn = self.turns[self._cursor]
            self._cursor += 1

        if kwargs.get("stream", False):
            return turn.to_stream()
        return turn.to_response()


class MockToolRegistry(ToolRegistry):
    """ToolRegistry variant backed by fixture results keyed by tool name and args."""

    def __init__(
        self,
        results: dict[tuple[str, tuple[tuple[str, Any], ...]], Any] | None = None,
        risk_levels: dict[str, str] | None = None,
    ):
        super().__init__()
        self.results = results or {}
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self._risk_levels = risk_levels or {}
        for name in sorted({key[0] for key in self.results} | set(self._risk_levels)):
            self._register_mock_tool(name)

    @classmethod
    def from_results(
        cls,
        results: dict[tuple[str, dict[str, Any]], Any],
        risk_levels: dict[str, str] | None = None,
    ) -> "MockToolRegistry":
        normalized = {
            (name, cls._freeze_args(args)): result
            for (name, args), result in results.items()
        }
        return cls(normalized, risk_levels=risk_levels)

    @staticmethod
    def _freeze_args(args: dict[str, Any]) -> tuple[tuple[str, Any], ...]:
        return tuple(sorted(args.items()))

    def _register_mock_tool(self, name: str) -> None:
        self.tools[name] = {
            "fn": None,
            "schema": {
                "type": "function",
                "function": {
                    "name": name,
                    "description": f"Mocked replay tool: {name}",
                    "parameters": {
                        "type": "object",
                        "properties": {},
                        "required": [],
                    },
                },
            },
            "description": f"Mocked replay tool: {name}",
            "risk_level": self._risk_levels.get(name, "low"),
            "plugin_name": "",
            "plugin_version": "",
            "namespace": "",
            "group": "replay",
        }

    def call(self, name: str, arguments: dict[str, Any]) -> Any:
        self.calls.append((name, dict(arguments)))
        key = (name, self._freeze_args(arguments))
        if key not in self.results:
            raise KeyError(f"No replay result for {name}({arguments!r})")
        result = self.results[key]
        if isinstance(result, Exception):
            raise result
        return result
