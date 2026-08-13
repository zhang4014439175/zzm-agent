"""验证最小 stdio MCP 客户端的握手、工具发现、调用和故障隔离。"""

from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from zzm_agent.cli_support import bootstrap
from zzm_agent.core.mcp_client import MCPError, StdioMCPClient
from zzm_agent.core.tool_registry import ToolRegistry


def _server(tmp_path):
    """创建一个遵循换行 JSON-RPC 的最小 MCP 服务，避免测试依赖网络或第三方包。"""
    path = tmp_path / "mcp_server.py"
    path.write_text('''import json, sys, time
mode = sys.argv[1] if len(sys.argv) > 1 else "normal"
for line in sys.stdin:
    request = json.loads(line)
    method = request["method"]
    if "id" not in request:
        continue
    if method == "initialize": result = {"serverInfo": {"name": "fixture"}}
    elif method == "tools/list":
        if mode == "notify":
            print(json.dumps({"jsonrpc": "2.0", "method": "notifications/message", "params": {"data": "ready"}}), flush=True)
        tools = [{"name": "greet", "description": "问候", "inputSchema": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"], "additionalProperties": False}}]
        if mode == "duplicate":
            tools.append({"name": "taken", "inputSchema": {"type": "object", "properties": {}}})
        result = {"tools": tools}
    elif method == "tools/call":
        if mode == "stderr":
            print("x" * 200000, file=sys.stderr, flush=True)
        if mode == "slow":
            time.sleep(0.2)
        result = {"content": [{"type": "text", "text": "hello " + request["params"]["arguments"]["name"]}]}
    elif method == "shutdown": result = {}
    else: result = {}
    print(json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": result}), flush=True)
''', encoding="utf-8")
    return path


def test_stdio_mcp_discovers_and_calls_tools_through_registry(tmp_path):
    """服务工具会进入统一注册表，因而仍校验参数并可由正常工具调用路径执行。"""
    registry = ToolRegistry()
    client = StdioMCPClient("fixture", [sys.executable, str(_server(tmp_path))])
    try:
        connection = client.connect(registry)
        assert connection.server_info == {"name": "fixture"}
        assert connection.tools == ["mcp.fixture.greet"]
        assert registry.get_tool_meta("mcp.fixture.greet")["risk_level"] == "medium"
        assert registry.call("mcp.fixture.greet", {"name": "Ada"}) == [
            {"type": "text", "text": "hello Ada"}
        ]
        with pytest.raises(TypeError, match="missing required"):
            registry.call("mcp.fixture.greet", {})
    finally:
        client.close()


def test_stdio_mcp_start_failure_is_isolated_from_local_tools():
    """无法启动的外部服务返回明确错误，且不会污染已有本地工具注册。"""
    registry = ToolRegistry()
    registry.tool("local")(lambda: "ok")
    client = StdioMCPClient("missing", ["definitely-not-a-real-mcp-command"])
    with pytest.raises(MCPError, match="Unable to start"):
        client.connect(registry)
    assert registry.call("<lambda>", {}) == "ok"


def test_stdio_mcp_allows_notifications_before_responses(tmp_path):
    """响应前出现合法通知时仍能完成发现，防止真实服务的日志通知打断握手。"""
    registry = ToolRegistry()
    client = StdioMCPClient(
        "fixture", [sys.executable, str(_server(tmp_path)), "notify"]
    )
    try:
        assert client.connect(registry).tools == ["mcp.fixture.greet"]
    finally:
        client.close()


def test_stdio_mcp_drains_stderr_and_times_out_without_reader_races(tmp_path):
    """大量 stderr 不会堵塞调用，超时也由唯一读线程安全返回。"""
    registry = ToolRegistry()
    noisy = StdioMCPClient(
        "noisy", [sys.executable, str(_server(tmp_path)), "stderr"],
        timeout_seconds=2,
    )
    try:
        noisy.connect(registry)
        assert registry.call("mcp.noisy.greet", {"name": "Ada"})
    finally:
        noisy.close()

    slow_registry = ToolRegistry()
    slow = StdioMCPClient(
        "slow", [sys.executable, str(_server(tmp_path)), "slow"],
        timeout_seconds=2,
    )
    try:
        slow.connect(slow_registry)
        slow.connection.timeout_seconds = 0.05
        with pytest.raises(MCPError, match="timed out"):
            slow_registry.call("mcp.slow.greet", {"name": "Ada"})
    finally:
        slow.close()


def test_stdio_mcp_rolls_back_partially_registered_tools(tmp_path):
    """发现中途名称冲突时撤销已注册代理，避免留下指向关闭服务的工具。"""
    registry = ToolRegistry()
    registry.register_external_tool(
        name="mcp.fixture.taken",
        description="existing",
        parameters={"type": "object", "properties": {}},
        handler=lambda: "existing",
    )
    client = StdioMCPClient(
        "fixture", [sys.executable, str(_server(tmp_path)), "duplicate"]
    )
    with pytest.raises(ValueError, match="already registered"):
        client.connect(registry)
    assert "mcp.fixture.greet" not in registry.tools
    assert "mcp.fixture.taken" in registry.tools


def test_invalid_mcp_timeout_is_isolated():
    """坏的超时配置只进入诊断状态，不阻止本地 Registry 完成启动。"""
    registry = ToolRegistry()
    bootstrap._load_mcp_servers(
        registry,
        {"mcp": {"servers": [{"name": "bad", "command": ["noop"], "timeout_seconds": "x"}]}},
    )
    assert registry.mcp_clients == []
    assert "could not convert string to float" in registry.mcp_errors[0]


def test_cli_main_closes_mcp_clients_on_exit(monkeypatch):
    """exec/REPL 正常返回后必须关闭 MCP 生命周期所有者。"""
    registry = ToolRegistry()
    closed = []
    registry.mcp_clients.append(SimpleNamespace(close=lambda: closed.append(True)))
    runtime = {"registry": registry}
    args = SimpleNamespace(command="repl", config_path=None, debug=False)
    monkeypatch.setattr(bootstrap, "parse_args", lambda _argv: args)
    monkeypatch.setattr(bootstrap, "ensure_first_run_config", lambda _args: None)
    monkeypatch.setattr(bootstrap, "load_config", lambda _path: {})
    monkeypatch.setattr(bootstrap, "ensure_model_credentials", lambda _cfg, _args: None)
    monkeypatch.setattr(bootstrap, "build_runtime", lambda _args, _cfg: runtime)
    monkeypatch.setattr("zzm_agent.cli_support.repl.run_repl", lambda _runtime: 0)

    assert bootstrap.main([]) == 0
    assert closed == [True]
