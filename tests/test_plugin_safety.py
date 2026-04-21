from pathlib import Path

from zzm_agent.core.tool_registry import ToolRegistry, set_active_registry


def build_plugin_registry() -> ToolRegistry:
    registry = ToolRegistry()
    set_active_registry(registry)
    plugin_dir = Path(__file__).resolve().parents[1] / "zzm_agent" / "plugins"
    registry.load_plugin_dir(plugin_dir)
    return registry


def test_write_file_cannot_escape_workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("ZZM_AGENT_WORKSPACE_ROOT", str(tmp_path))
    registry = build_plugin_registry()

    result = registry.call(
        "write_file",
        {"path": str(tmp_path.parent / "outside.txt"), "content": "blocked"},
    )

    assert "Error writing to file" in result
    assert "escapes workspace root" in result


def test_read_file_can_access_workspace_file(tmp_path, monkeypatch):
    monkeypatch.setenv("ZZM_AGENT_WORKSPACE_ROOT", str(tmp_path))
    target = tmp_path / "note.txt"
    target.write_text("hello", encoding="utf-8")
    registry = build_plugin_registry()

    result = registry.call("read_file", {"path": str(target)})

    assert "hello" in result
    assert "note.txt" in result
