import argparse
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
        
        # setup workspace
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            for path_str, content in case.get("initial_files", {}).items():
                p = workspace / path_str
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(content, encoding="utf-8")

            if suite == "replay":
                passed = _run_replay(case, workspace)
            else:
                if not use_llm:
                    print("Skipping LLM eval because --llm was not provided.")
                    return 1
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
        if "error" in m:
            error_msg = m["error"]
            error_type = m.get("error_type", "ToolError")
            recovery_hint = m.get("recovery_hint", "")
            mock_results[key] = ToolError(error_type=error_type, message=error_msg, recovery_hint=recovery_hint, retryable=False)
        else:
            mock_results[key] = m.get("result", "")
            
    registry = MockToolRegistry(mock_results)
    
    loop = AgentLoop(
        client=client,
        model="replay-model",
        system_prompt="You are a deterministic replay agent.",
        registry=registry,
        store=MemoryStore(path=workspace / "memory.json", max_history=20)
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
        
    return True

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
