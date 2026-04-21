# Change Summary: Start E2 Agent Execution Safety

**Date**: 2026-04-21  
**Identifier**: e2-agent-execution-safety

## 1. Problem Statement

After v2 reached a usable feature baseline, the next roadmap stage is E2: make agent execution safer before adding more complex planning, prompt, memory, or async behavior. The current agent loop could continue tool calls indefinitely, repeated identical tool calls were not detected, tool parameter schemas lacked docstring argument descriptions, and unexpected tool exceptions were returned as unstructured text.

## 2. Technical Solution

### 2.1 Tool Loop Guardrails
- Added `max_tool_iterations` to `AgentLoop`, defaulting to 20.
- Added `duplicate_tool_call_limit` to stop consecutive identical single-tool calls.
- Added `agent.max_tool_iterations` and `agent.duplicate_tool_call_limit` config fields so loop safety policy can be tuned without code changes.
- When a guardrail trips, the agent persists a clear assistant message and stops the current turn.

### 2.2 Tool Schema Quality
- Added Args-section docstring parsing in `ToolRegistry`.
- Generated tool schemas now include parameter descriptions when tool docstrings provide them.

### 2.3 Structured Tool Errors
- Added `zzm_agent.core.errors.ToolError` and `CommandTimeoutError`.
- Unexpected tool execution exceptions are returned to the model as JSON with `error_type`, `message`, `recovery_hint`, and `retryable`.
- Direct `ToolRegistry.call()` behavior remains unchanged so existing plugin APIs stay compatible.

## 3. Validation Results

### 3.1 Automated Tests
- Added coverage for max tool iteration stopping.
- Added coverage for repeated tool-call detection.
- Added coverage for legacy default and configured loop safety policy values.
- Added coverage for structured tool exception payloads.
- Added coverage for docstring argument descriptions in schemas.

### 3.2 Test Output

```text
pytest tests -q --basetemp C:\Users\zhangzm\.codex\memories\zzm-agent-pytest-e2-config-all
104 passed in 13.69s
```
