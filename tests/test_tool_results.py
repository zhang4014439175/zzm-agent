from zzm_agent.core.runtime_records import EventBus
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
