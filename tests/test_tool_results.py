from zzm_agent.core.runtime_records import EventBus
from zzm_agent.core.local_tool_renderers import (
    FileEditRenderer,
    FileReadRenderer,
    SearchRenderer,
    ShellRenderer,
    build_local_tool_renderer_registry,
    parse_tool_arguments,
)
from zzm_agent.core.tool_results import (
    DisplayMode,
    DisplayPolicy,
    PlainTextToolRenderer,
    RendererRegistry,
    RenderedToolView,
    ToolProgressEmitter,
    ToolRenderContext,
    ToolResult,
)


def test_tool_result_separates_model_content_display_preview_and_record():
    content = "line 1\nline 2\nline 3"

    result = ToolResult.from_text(
        tool_call_id="call-1",
        tool_name="read_file",
        status="success",
        content=content,
        policy=DisplayPolicy(max_preview_chars=12, max_preview_lines=2),
        metadata={"path": "app.py"},
    )
    restored = ToolResult.from_record(result.to_record())

    assert result.model_content == content
    assert result.to_model_message()["content"] == content
    assert result.display_content["text"] == "line 1\nline "
    assert result.display_content["truncated"] is True
    assert result.display_mode is DisplayMode.COLLAPSED
    assert restored.metadata["path"] == "app.py"


def test_tool_progress_emitter_sequences_events_and_caps_buffer():
    bus = EventBus()
    observed = []
    bus.subscribe(lambda event: observed.append(event), event_type="tool.progress")
    emitter = ToolProgressEmitter(bus, max_buffered_events=2)

    first = emitter.emit(tool_call_id="call-1", message="starting")
    second = emitter.emit(tool_call_id="call-1", percent=50, stdout_chunk="half")
    third = emitter.emit(tool_call_id="call-1", percent=100, message="done")

    assert [first.sequence, second.sequence, third.sequence] == [1, 2, 3]
    assert [event.sequence for event in emitter.events_for("call-1")] == [2, 3]
    assert [event.payload["sequence"] for event in observed] == [1, 2, 3]


def test_renderer_registry_selects_tool_category_source_and_default_renderers():
    class DemoRenderer:
        def render_use(self, context):
            return RenderedToolView(text=f"use:{context.tool_name}")

        def render_progress(self, context, event):
            return RenderedToolView(text=f"progress:{event.sequence}")

        def render_result(self, context, result):
            return RenderedToolView(text=f"result:{result.tool_name}")

        def render_error(self, context, result):
            return RenderedToolView(text=f"error:{result.tool_name}")

    registry = RendererRegistry()
    tool_renderer = DemoRenderer()
    category_renderer = DemoRenderer()
    source_renderer = DemoRenderer()
    registry.register_tool("read_file", tool_renderer)
    registry.register_category("search", category_renderer)
    registry.register_source("mcp", source_renderer)

    assert registry.select(ToolRenderContext("read_file", "call-1")) is tool_renderer
    assert registry.select(ToolRenderContext("rg", "call-2", category="search")) is category_renderer
    assert registry.select(ToolRenderContext("remote", "call-3", source="mcp")) is source_renderer
    assert isinstance(registry.select(ToolRenderContext("unknown", "call-4")), PlainTextToolRenderer)


def test_plain_text_renderer_reports_truncation_and_artifacts():
    """验证未知工具仍能展示折叠信息和完整结果引用，防止专用集合破坏降级路径。"""
    renderer = PlainTextToolRenderer()
    context = ToolRenderContext(tool_name="run_shell", tool_call_id="call-1")
    result = ToolResult.from_text(
        tool_call_id="call-1",
        tool_name="run_shell",
        status="success",
        content="abcdef",
        policy=DisplayPolicy(max_preview_chars=3),
        artifacts=[{"artifact_id": "artifact-1"}],
    )

    view = renderer.render_result(context, result)

    assert view.display_mode is DisplayMode.COLLAPSED
    assert "abc" in view.text
    assert "truncated" in view.text
    assert "artifact-1" in view.text


def test_local_renderer_registry_maps_builtin_tools_and_keeps_default_fallback():
    """验证文件、编辑、搜索和 Shell 使用专用 Renderer，插件工具仍安全降级。"""
    registry = build_local_tool_renderer_registry()

    assert isinstance(registry.select(ToolRenderContext("read_file", "1")), FileReadRenderer)
    assert isinstance(registry.select(ToolRenderContext("file_edit", "2")), FileEditRenderer)
    assert isinstance(registry.select(ToolRenderContext("grep_search", "3")), SearchRenderer)
    assert isinstance(registry.select(ToolRenderContext("run_shell", "4")), ShellRenderer)
    assert isinstance(
        registry.select(ToolRenderContext("plugin_tool", "5")),
        PlainTextToolRenderer,
    )


def test_local_renderers_use_structured_arguments_for_dynamic_activity():
    """验证活动描述来自结构化参数而非结果自然语言，并避免输出完整写入正文。"""
    registry = build_local_tool_renderer_registry()
    read_context = ToolRenderContext(
        "read_file",
        "1",
        {"path": "src/app.py", "start_line": 10, "end_line": 20},
    )
    edit_context = ToolRenderContext(
        "write_file",
        "2",
        {"path": "notes.txt", "content": "secret body"},
    )
    search_context = ToolRenderContext(
        "grep_search",
        "3",
        {"pattern": "CompletionGate", "path": "zzm_agent", "include": "*.py"},
    )

    assert registry.select(read_context).render_use(read_context).text == (
        "读取 src/app.py（行 10-20）"
    )
    edit_text = registry.select(edit_context).render_use(edit_context).text
    assert edit_text == "修改 notes.txt（11 字符）"
    assert "secret body" not in edit_text
    assert registry.select(search_context).render_use(search_context).text == (
        "搜索 'CompletionGate' 于 zzm_agent（*.py）"
    )


def test_shell_renderer_reports_exit_code_from_structured_result():
    """验证 Shell 展示保留输出预览并单独暴露退出码，防止 UI 误猜执行状态。"""
    renderer = ShellRenderer()
    context = ToolRenderContext(
        "run_shell",
        "call-shell",
        {"command": "pytest -q", "cwd": "tests"},
    )
    result = ToolResult.from_text(
        tool_call_id="call-shell",
        tool_name="run_shell",
        status="success",
        content="[stdout]\n2 passed\n\n[exit code: 0]",
    )

    assert renderer.render_use(context).text == "运行 pytest -q（cwd: tests）"
    view = renderer.render_result(context, result)
    assert "2 passed" in view.text
    assert view.metadata["exit_code"] == 0


def test_parse_tool_arguments_waits_for_complete_json_objects():
    """验证半段流式 JSON 不会抛错，完整对象才进入动态活动描述。"""
    assert parse_tool_arguments('{"path":') == {}
    assert parse_tool_arguments('["not", "an", "object"]') == {}
    assert parse_tool_arguments('{"path": "app.py"}') == {"path": "app.py"}
