import argparse
import json
import os
import shutil
import sys
import tempfile
import yaml
from pathlib import Path
from typing import Any

from zzm_agent.core.agent_loop import AgentLoop
from zzm_agent.core.errors import ToolError
from zzm_agent.memory.store import MemoryStore
from zzm_agent.eval.replay import MockToolRegistry, ReplayLLM, ReplayTurn, ReplayToolCall


class _ReplayMemoryStore:
    """In-memory store for deterministic replay evals."""

    def __init__(self):
        self.history: list[dict[str, Any]] = []
        self.latest_context: dict[str, Any] | None = None

    def load_history(self) -> list[dict[str, Any]]:
        return list(self.history)

    def build_turn_messages(
        self,
        *,
        system_prompt: str,
        user_input: str,
        memory_limit: int,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        return [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_input},
        ], {}

    def append(self, messages: list[dict[str, Any]]) -> None:
        self.history.extend(messages)

    def save_latest_context(self, payload: dict[str, Any]) -> None:
        self.latest_context = payload

def run_eval(suite: str, use_llm: bool, config: dict[str, Any]) -> int:
    benchmarks_dir = Path(__file__).parent / "benchmarks"
    if not benchmarks_dir.exists():
        print("No benchmarks found.")
        return 1

    success_count = 0
    total_count = 0

    for file in sorted(benchmarks_dir.glob("*.yaml")):
        with open(file, "r", encoding="utf-8") as f:
            case = yaml.safe_load(f)
        
        print(f"Running benchmark: {case['name']} ({file.name})")
        total_count += 1
        
        if suite == "replay":
            # Replay uses MockToolRegistry fixtures and does not need a real
            # workspace. Avoid filesystem writes so deterministic eval remains
            # stable in restricted Windows environments.
            passed = _run_replay(case, Path.cwd())
        else:
            if not use_llm:
                print("Skipping LLM eval because --llm was not provided.")
                return 1

            # setup workspace
            temp_root = Path(config.get("eval", {}).get("temp_root", Path.cwd()))
            temp_root.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(
                prefix=".tmp_eval_",
                dir=temp_root,
                ignore_cleanup_errors=True,
            ) as tmpdir:
                workspace = Path(tmpdir)
                for path_str, content in case.get("initial_files", {}).items():
                    p = workspace / path_str
                    p.parent.mkdir(parents=True, exist_ok=True)
                    p.write_text(content, encoding="utf-8")

                passed = _run_llm(case, workspace, config)
        if passed:
            success_count += 1
            print("  [PASSED]")
        else:
            print("  [FAILED]")

    print(f"\nEval completed: {success_count}/{total_count} passed.")
    print(f"Metrics: Success Rate: {(success_count/total_count)*100:.1f}%, Tool Calls Evaluated: {total_count}")
    return 0 if success_count == total_count else 1

def _run_replay(case: dict[str, Any], workspace: Path) -> bool:
    turns = []
    for t in case.get("replay_turns", []):
        tool_calls = [ReplayToolCall(tc["name"], tc.get("arguments", {})) for tc in t.get("tool_calls", [])]
        turns.append(ReplayTurn(content=t.get("content", ""), tool_calls=tool_calls))
    
    client = ReplayLLM(turns)
    
    mock_results = {}
    for m in case.get("mock_tool_results", []):
        name = m["name"]
        args = m.get("arguments", {})
        key = (name, tuple(sorted(args.items())))
        if "exception" in m:
            mock_results[key] = _exception_from_mock(m)
        elif "error" in m:
            error_msg = m["error"]
            error_type = m.get("error_type", "ToolError")
            recovery_hint = m.get("recovery_hint", "")
            mock_results[key] = ToolError(
                error_type=error_type,
                message=error_msg,
                recovery_hint=recovery_hint,
                retryable=m.get("retryable", False),
                category=m.get("category", "unknown"),
                deterministic=m.get("deterministic", True),
                retry_after_seconds=m.get("retry_after_seconds"),
            )
        else:
            mock_results[key] = m.get("result", "")
            
    registry = MockToolRegistry(mock_results)
    retry_delays: list[float] = []
    
    loop = AgentLoop(
        client=client,
        model="replay-model",
        system_prompt="You are a deterministic replay agent.",
        registry=registry,
        store=_ReplayMemoryStore(),
        max_tool_retries=case.get("max_tool_retries", 1),
        retry_base_delay=case.get("retry_base_delay", 0.25),
        retry_max_delay=case.get("retry_max_delay", 5.0),
        retry_sleep=retry_delays.append,
    )
    
    result = loop.run(case["task"], stream=False)
    
    expected = case.get("expected", {})
    expected_calls = expected.get("expected_tool_calls", [])
    forbidden_calls = expected.get("forbidden_tool_calls", [])
    output_match = expected.get("output_match", "")
    
    actual_calls = [c[0] for c in registry.calls]
    
    for ec in expected_calls:
        if ec not in actual_calls:
            print(f"  Expected tool call '{ec}' not found. Actual: {actual_calls}")
            return False
            
    for fc in forbidden_calls:
        if fc in actual_calls:
            print(f"  Forbidden tool call '{fc}' found.")
            return False
            
    if output_match and output_match not in result:
        print(f"  Output match '{output_match}' not found in result: {result}")
        return False

    if not _check_extended_replay_expectations(
        expected=expected,
        loop=loop,
        client=client,
        registry=registry,
        retry_delays=retry_delays,
    ):
        return False
        
    return True


def _exception_from_mock(mock: dict[str, Any]) -> Exception:
    """Build a deterministic exception from benchmark YAML."""
    exception_name = mock["exception"]
    message = mock.get("message", mock.get("error", exception_name))
    if exception_name == "FileNotFoundError":
        return FileNotFoundError(message)
    if exception_name == "PermissionError":
        return PermissionError(message)
    if exception_name == "TimeoutError":
        return TimeoutError(message)
    if exception_name == "ConnectionError":
        exc = ConnectionError(message)
        if "retry_after_seconds" in mock:
            exc.retry_after_seconds = mock["retry_after_seconds"]
        if "retry_after" in mock:
            exc.retry_after = mock["retry_after"]
        return exc
    if exception_name == "TypeError":
        return TypeError(message)
    return RuntimeError(message)


def _check_extended_replay_expectations(
    *,
    expected: dict[str, Any],
    loop: AgentLoop,
    client: ReplayLLM,
    registry: MockToolRegistry,
    retry_delays: list[float],
) -> bool:
    if "model_call_count" in expected and client.call_count != expected["model_call_count"]:
        print(
            f"  Expected model_call_count={expected['model_call_count']}, "
            f"actual={client.call_count}"
        )
        return False

    if (
        "reflection_count" in expected
        and loop.last_reflection_count != expected["reflection_count"]
    ):
        print(
            f"  Expected reflection_count={expected['reflection_count']}, "
            f"actual={loop.last_reflection_count}"
        )
        return False

    if "progress_reason" in expected:
        actual_reason = (
            loop.last_progress_signal.reason if loop.last_progress_signal else None
        )
        if actual_reason != expected["progress_reason"]:
            print(
                f"  Expected progress_reason={expected['progress_reason']}, "
                f"actual={actual_reason}"
            )
            return False

    if "tool_call_count" in expected and len(registry.calls) != expected["tool_call_count"]:
        print(
            f"  Expected tool_call_count={expected['tool_call_count']}, "
            f"actual={len(registry.calls)}"
        )
        return False

    expected_sequence = expected.get("tool_call_sequence", [])
    if expected_sequence:
        actual_sequence = [
            {"name": name, "arguments": arguments}
            for name, arguments in registry.calls
        ]
        if actual_sequence != expected_sequence:
            print(
                f"  Expected tool_call_sequence={expected_sequence}, "
                f"actual={actual_sequence}"
            )
            return False

    for text in expected.get("runtime_prompt_contains", []):
        if not _requests_contain_text(client.requests, text):
            print(f"  Runtime prompt text '{text}' not found in model requests.")
            return False

    for expected_delay in expected.get("retry_delays", []):
        if expected_delay not in retry_delays:
            print(
                f"  Expected retry delay {expected_delay} not found. "
                f"Actual retry_delays={retry_delays}"
            )
            return False

    for expected_json in expected.get("tool_result_json_contains", []):
        if not _history_has_tool_json(loop.store.load_history(), expected_json):
            print(f"  Expected tool JSON fragment not found: {expected_json}")
            return False

    return True


def _requests_contain_text(requests: list[dict[str, Any]], text: str) -> bool:
    for request in requests:
        for message in request.get("messages", []):
            if text in str(message.get("content", "")):
                return True
    return False


def _history_has_tool_json(
    history: list[dict[str, Any]],
    expected_fragment: dict[str, Any],
) -> bool:
    for message in history:
        if message.get("role") != "tool":
            continue
        try:
            payload = json.loads(message.get("content", ""))
        except (TypeError, json.JSONDecodeError):
            continue
        if all(payload.get(key) == value for key, value in expected_fragment.items()):
            return True
    return False

def _run_llm(case: dict[str, Any], workspace: Path, config: dict[str, Any]) -> bool:
    from openai import OpenAI
    from zzm_agent.cli_support.runtime import build_registry, get_agent_loop_policy
    
    api_key = config["model"].get("api_key") or os.environ.get("ZZM_AGENT_API_KEY") or os.environ.get("OPENAI_API_KEY")
    client = OpenAI(base_url=config["model"]["base_url"], api_key=api_key)
    
    original_workspace = os.environ.get("ZZM_AGENT_WORKSPACE_ROOT")
    os.environ["ZZM_AGENT_WORKSPACE_ROOT"] = str(workspace)
    try:
        registry = build_registry(config)
        store = MemoryStore(path=workspace / "memory.json", max_history=20)
        loop_policy = get_agent_loop_policy(config)
        
        loop = AgentLoop(
            client=client,
            model=config["model"]["model_name"],
            system_prompt=config["agent"]["system_prompt"],
            registry=registry,
            store=store,
            max_tool_iterations=loop_policy["max_tool_iterations"],
            duplicate_tool_call_limit=loop_policy["duplicate_tool_call_limit"],
            max_tool_retries=loop_policy["max_tool_retries"],
            auto_approve=True
        )
        
        result = loop.run(case["task"], stream=False)
    finally:
        if original_workspace:
            os.environ["ZZM_AGENT_WORKSPACE_ROOT"] = original_workspace
        else:
            del os.environ["ZZM_AGENT_WORKSPACE_ROOT"]
            
    expected = case.get("expected", {})
    output_match = expected.get("output_match", "")
    
    if output_match and output_match not in result:
        print(f"  Output match '{output_match}' not found in result: {result}")
        return False
        
    return True
