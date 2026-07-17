from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from zzm_agent.cli_support.rendering import PlainTextRenderer
from zzm_agent.core.agent_loop import AgentLoop
from zzm_agent.core.change_set import ChangeSetStore
from zzm_agent.core.model_stream import ModelStreamEventKind
from zzm_agent.core.observability import tool_end_event, tool_start_event
from zzm_agent.core.query_engine import QueryEngine
from zzm_agent.core.runtime_state import CancellationError, CancellationToken
from zzm_agent.core.sandbox import SandboxProfile, SandboxViolation
from zzm_agent.core.tool_registry import ToolArgumentValidationError, ToolRegistry
from zzm_agent.memory.store import MemoryStore


class Console:
    """收集纯文本 Renderer 输出，供阶段验收断言用户可见内容。"""

    def __init__(self) -> None:
        """初始化空输出列表；测试结束前不会写入真实终端。"""
        self.lines: list[str] = []

    def print(self, value="") -> None:
        """记录一次终端输出；输入会转为字符串，返回值固定为空。"""
        self.lines.append(str(value))


def _response(content=None, tool_calls=None, finish_reason=None):
    """构造最小模型响应，用于控制工具轮次、正文和 Provider 结束原因。"""
    message = SimpleNamespace(content=content, tool_calls=tool_calls or [])
    choice = SimpleNamespace(message=message, finish_reason=finish_reason)
    return SimpleNamespace(choices=[choice], usage=None, error=None)


def _tool_call(call_id: str, name: str, arguments: str = "{}"):
    """构造 OpenAI 兼容工具调用对象，参数保持原始 JSON 字符串。"""
    return SimpleNamespace(
        id=call_id,
        type="function",
        function=SimpleNamespace(name=name, arguments=arguments),
    )


def _file_event(call_id: str, path: str, *, result: bool = False):
    """构造文件编辑开始或成功事件，供 ChangeSet 捕获前后内容。"""
    arguments = {"path": path, "target": "old", "replacement": "new"}
    if not result:
        return tool_start_event(
            tool_name="file_edit",
            tool_call_id=call_id,
            arguments=arguments,
            risk_level="medium",
        )
    return tool_end_event(
        tool_name="file_edit",
        tool_call_id=call_id,
        arguments=arguments,
        risk_level="medium",
        status="success",
        duration_ms=1,
        result="Success",
        attempts=1,
    )


def test_p3_permission_validation_and_sandbox_form_a_deterministic_boundary(tmp_path):
    """阶段验收：无效参数、越界路径和敏感文件在执行前被稳定拒绝。"""
    profile = SandboxProfile(workspace_roots=(tmp_path,))
    registry = ToolRegistry()
    executed: list[str] = []

    @registry.tool(description="write one workspace file", risk_level="high")
    def write_target(path: str, content: str) -> str:
        """记录模拟写入；只有参数和沙箱均通过时才应进入此函数。"""
        executed.append(path)
        return content

    with pytest.raises(ToolArgumentValidationError, match="unknown parameter"):
        registry.call(
            "write_target",
            {"path": "demo.txt", "content": "ok", "invented": True},
        )
    with pytest.raises(SandboxViolation, match="Sensitive"):
        profile.authorize_path(tmp_path / ".env", access="read")
    with pytest.raises(SandboxViolation, match="escapes workspace"):
        profile.authorize_path(tmp_path.parent / "outside.txt", access="write")

    assert executed == []
    assert profile.authorize_path("src/app.py", access="write") == (
        tmp_path / "src" / "app.py"
    )


def test_p3_file_changes_are_reversible_without_overwriting_user_edits(tmp_path):
    """阶段验收：Agent 修改可撤销，用户后续编辑会转为冲突而非被覆盖。"""
    target = tmp_path / "demo.txt"
    target.write_text("old", encoding="utf-8")
    store = ChangeSetStore(tmp_path, session_id="acceptance")

    store.capture_start(_file_event("call-1", "demo.txt"))
    target.write_text("new", encoding="utf-8")
    first = store.capture_end(_file_event("call-1", "demo.txt", result=True))
    assert first is not None
    assert store.undo(first.change_set_id).undone is True
    assert target.read_text(encoding="utf-8") == "old"

    store.capture_start(_file_event("call-2", "demo.txt"))
    target.write_text("agent value", encoding="utf-8")
    second = store.capture_end(_file_event("call-2", "demo.txt", result=True))
    assert second is not None
    target.write_text("user value", encoding="utf-8")
    conflict = store.undo(second.change_set_id)

    assert conflict.undone is False
    assert "conflict" in conflict.message.lower()
    assert target.read_text(encoding="utf-8") == "user value"


def test_p3_cancellation_blocks_execution_and_cleanup_remains_lifo():
    """阶段验收：取消在执行前生效，正常或失败工具均按逆序完成资源清理。"""
    registry = ToolRegistry()
    events: list[str] = []

    @registry.tool(description="use a managed resource")
    def managed(fail: str = "false") -> str:
        """模拟资源使用；失败用于证明 cleanup 不依赖成功返回。"""
        events.append("execute")
        if fail == "true":
            raise ValueError("boom")
        return "ok"

    registry.register_cleanup("managed", lambda *_: events.append("close-inner"))
    registry.register_cleanup("managed", lambda *_: events.append("close-outer"))
    token = CancellationToken(token_id="tool:p3", scope="tool")
    token.cancel("user_cancelled")

    with pytest.raises(CancellationError):
        registry.call("managed", {}, cancellation_token=token)
    assert events == []

    assert registry.call("managed", {}) == "ok"
    assert events == ["execute", "close-outer", "close-inner"]
    events.clear()
    with pytest.raises(ValueError, match="boom"):
        registry.call("managed", {"fail": "true"})
    assert events == ["execute", "close-outer", "close-inner"]


def test_p3_long_result_is_artifactized_and_all_end_states_are_visible(tmp_path):
    """阶段验收：长结果不污染上下文，续段完成及重复阻塞都有终止证据。"""
    registry = ToolRegistry()
    full_output = "evidence-line\n" * 1500

    @registry.tool(description="collect a large local report")
    def collect_report() -> str:
        """返回超长本地结果，触发 Artifact、让出、压缩和自动续段。"""
        return full_output

    store = MemoryStore(
        path=tmp_path / "memory.json",
        max_history=30,
        max_context_tokens=500,
        compression_keep_recent=1,
    )
    loop = AgentLoop(
        client=MagicMock(),
        model="test-model",
        system_prompt="sys",
        registry=registry,
        store=store,
        max_tool_iterations=1,
        max_inline_tool_result_tokens=20,
    )
    loop.client.chat.completions.create.side_effect = [
        _response(
            tool_calls=[_tool_call("report-1", "collect_report")],
            finish_reason="tool_calls",
        ),
        _response(content="报告已检查。", finish_reason="stop"),
    ]
    result = QueryEngine(
        agent_loop=loop,
        config={"agent": {"max_auto_continuations": 3}},
    ).submit_message("读取报告并完成结论", stream=False)

    assert [segment.status.value for segment in result.segments] == [
        "yielded",
        "completed",
    ]
    artifact = result.segments[0].checkpoint["artifacts"][0]
    assert loop.artifact_store.read_text(artifact["artifact_id"]) == full_output
    second_messages = loop.client.chat.completions.create.call_args_list[1].kwargs[
        "messages"
    ]
    assert full_output not in str(second_messages)
    assert any(
        event.kind is ModelStreamEventKind.TERMINATION
        and event.metadata["status"] == "completed"
        for event in result.events
    )

    console = Console()
    renderer = PlainTextRenderer(console)
    for event in result.events:
        renderer.render_event(event)
    renderer.finish(result.reply)
    assert any(line.startswith("Ran collect_report:") for line in console.lines)
    assert any("Ended: completed" in line for line in console.lines)
    assert "报告已检查。" in console.lines
