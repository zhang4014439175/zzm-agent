from pathlib import Path
import os

import pytest

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


def _make_dir_symlink(link_path: Path, target_path: Path) -> None:
    try:
        os.symlink(target_path, link_path, target_is_directory=True)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"Directory symlinks are not available: {exc}")


def test_write_file_cannot_escape_through_symlink_parent(tmp_path, monkeypatch):
    monkeypatch.setenv("ZZM_AGENT_WORKSPACE_ROOT", str(tmp_path))
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir()
    link_path = tmp_path / "linked"
    _make_dir_symlink(link_path, outside)
    registry = build_plugin_registry()

    result = registry.call(
        "write_file",
        {"path": "linked/escape.txt", "content": "blocked"},
    )

    assert "Error writing to file" in result
    assert "escapes workspace root" in result
    assert not (outside / "escape.txt").exists()


def test_search_cannot_read_direct_symlink_escape(tmp_path, monkeypatch):
    monkeypatch.setenv("ZZM_AGENT_WORKSPACE_ROOT", str(tmp_path))
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("outside-secret", encoding="utf-8")
    link_path = tmp_path / "linked"
    _make_dir_symlink(link_path, outside)
    registry = build_plugin_registry()

    result = registry.call(
        "grep_search",
        {"pattern": "outside-secret", "path": "linked/secret.txt"},
    )

    assert "Error searching" in result
    assert "escapes workspace root" in result
