"""Tests for the enhanced file_ops, search, and shell plugins."""

import os
import pytest
from pathlib import Path
from unittest.mock import patch

from zzm_agent.core.tool_registry import ToolRegistry, set_active_registry


@pytest.fixture(autouse=True)
def workspace(tmp_path, monkeypatch):
    """Set up a fake workspace root for all tests."""
    monkeypatch.setenv("ZZM_AGENT_WORKSPACE_ROOT", str(tmp_path))
    return tmp_path


@pytest.fixture
def registry(workspace):
    """Load the plugin modules into a fresh registry."""
    reg = ToolRegistry()
    set_active_registry(reg)
    # Import plugins after setting the active registry so @tool decorators bind correctly
    import importlib
    import zzm_agent.plugins.file_ops as file_ops_mod
    import zzm_agent.plugins.search as search_mod
    import zzm_agent.plugins.shell as shell_mod
    importlib.reload(file_ops_mod)
    importlib.reload(search_mod)
    importlib.reload(shell_mod)
    return reg


# ─── read_file ───────────────────────────────────────────────────────────────


class TestReadFile:
    def test_read_file_with_line_numbers(self, registry, workspace):
        f = workspace / "hello.py"
        f.write_text("line1\nline2\nline3\nline4\nline5\n", encoding="utf-8")
        result = registry.call("read_file", {"path": str(f)})
        assert "hello.py" in result
        assert "1:" in result
        assert "line1" in result
        assert "Lines 1-5 of 5" in result

    def test_read_file_range(self, registry, workspace):
        f = workspace / "range.txt"
        f.write_text("\n".join(f"line{i}" for i in range(1, 21)), encoding="utf-8")
        result = registry.call("read_file", {"path": str(f), "start_line": 5, "end_line": 10})
        assert "Lines 5-10 of 20" in result
        assert "line5" in result
        assert "line10" in result
        # Lines outside the range should not appear
        assert "line1:" not in result
        assert "line11" not in result

    def test_read_file_not_found(self, registry, workspace):
        result = registry.call("read_file", {"path": str(workspace / "nope.txt")})
        assert "Error" in result
        assert "not found" in result

    def test_read_file_empty(self, registry, workspace):
        f = workspace / "empty.txt"
        f.write_text("", encoding="utf-8")
        result = registry.call("read_file", {"path": str(f)})
        assert "empty" in result.lower()

    def test_read_file_records_cache_and_ranges(self, registry, workspace):
        import zzm_agent.plugins.file_ops as file_ops_mod

        f = workspace / "cached.txt"
        f.write_text("line1\nline2\nline3\n", encoding="utf-8")

        registry.call("read_file", {"path": str(f), "start_line": 1, "end_line": 2})
        registry.call("read_file", {"path": str(f), "start_line": 2, "end_line": 3})
        cache = file_ops_mod.get_file_state_cache()
        state = cache.files[str(f.resolve(strict=False))]

        assert state.line_count == 3
        assert state.content == "line1\nline2\nline3\n"
        assert [(item.start_line, item.end_line) for item in state.read_ranges] == [
            (1, 2),
            (2, 3),
        ]

    def test_read_file_invalidates_cache_after_external_change(self, registry, workspace):
        import zzm_agent.plugins.file_ops as file_ops_mod

        f = workspace / "external.txt"
        f.write_text("old\n", encoding="utf-8")
        registry.call("read_file", {"path": str(f)})

        f.write_text("new content\n", encoding="utf-8")
        result = registry.call("read_file", {"path": str(f)})
        cache = file_ops_mod.get_file_state_cache()
        state = cache.files[str(f.resolve(strict=False))]

        assert "new content" in result
        assert state.content == "new content\n"
        assert state.content_hash


# ─── file_edit ───────────────────────────────────────────────────────────────


class TestFileEdit:
    def test_single_replacement(self, registry, workspace):
        f = workspace / "code.py"
        f.write_text("def hello():\n    print('hello')\n    print('hello')\n", encoding="utf-8")
        result = registry.call("file_edit", {
            "path": str(f),
            "target": "print('hello')",
            "replacement": "print('world')",
        })
        assert "Success" in result
        assert "1 occurrence" in result
        content = f.read_text(encoding="utf-8")
        # Only the first occurrence should be replaced
        assert content.count("print('world')") == 1
        assert content.count("print('hello')") == 1

    def test_replace_all(self, registry, workspace):
        f = workspace / "code.py"
        f.write_text("foo\nbar\nfoo\nbaz\nfoo\n", encoding="utf-8")
        result = registry.call("file_edit", {
            "path": str(f),
            "target": "foo",
            "replacement": "qux",
            "replace_all": "true",
        })
        assert "3 occurrence" in result
        content = f.read_text(encoding="utf-8")
        assert content.count("qux") == 3
        assert "foo" not in content

    def test_target_not_found(self, registry, workspace):
        f = workspace / "code.py"
        f.write_text("hello world\n", encoding="utf-8")
        result = registry.call("file_edit", {
            "path": str(f),
            "target": "not_here",
            "replacement": "x",
        })
        assert "Error" in result
        assert "not found" in result
        # Should provide a hint
        assert "Hint" in result or "hello world" in result

    def test_multiline_replacement(self, registry, workspace):
        f = workspace / "config.yaml"
        original = "key1: value1\nkey2: old_value\nkey3: value3\n"
        f.write_text(original, encoding="utf-8")
        result = registry.call("file_edit", {
            "path": str(f),
            "target": "key2: old_value",
            "replacement": "key2: new_value",
        })
        assert "Success" in result
        assert f.read_text(encoding="utf-8") == "key1: value1\nkey2: new_value\nkey3: value3\n"

    def test_file_edit_updates_file_cache_after_agent_write(self, registry, workspace):
        import zzm_agent.plugins.file_ops as file_ops_mod

        f = workspace / "cache_edit.txt"
        f.write_text("hello\n", encoding="utf-8")

        registry.call("file_edit", {
            "path": str(f),
            "target": "hello",
            "replacement": "world",
        })
        state = file_ops_mod.get_file_state_cache().files[str(f.resolve(strict=False))]

        assert state.content == "world\n"
        assert state.agent_last_modified_at is not None


# ─── file_append ─────────────────────────────────────────────────────────────


class TestFileAppend:
    def test_append_to_existing(self, registry, workspace):
        f = workspace / "log.txt"
        f.write_text("line1\n", encoding="utf-8")
        result = registry.call("file_append", {"path": str(f), "content": "line2\n"})
        assert "Success" in result
        assert f.read_text(encoding="utf-8") == "line1\nline2\n"

    def test_append_creates_file(self, registry, workspace):
        f = workspace / "new.txt"
        result = registry.call("file_append", {"path": str(f), "content": "hello\n"})
        assert "Success" in result
        assert f.read_text(encoding="utf-8") == "hello\n"


# ─── list_directory ──────────────────────────────────────────────────────────


class TestListDirectory:
    def test_list_basic(self, registry, workspace):
        (workspace / "file_a.py").write_text("a", encoding="utf-8")
        (workspace / "file_b.txt").write_text("b", encoding="utf-8")
        (workspace / "subdir").mkdir()
        (workspace / "subdir" / "c.py").write_text("c", encoding="utf-8")

        result = registry.call("list_directory", {"path": str(workspace)})
        assert "file_a.py" in result
        assert "file_b.txt" in result
        assert "subdir/" in result
        assert "3 items" in result or "items" in result

    def test_list_skips_hidden(self, registry, workspace):
        (workspace / ".hidden").mkdir()
        (workspace / "visible.txt").write_text("v", encoding="utf-8")
        result = registry.call("list_directory", {"path": str(workspace)})
        assert ".hidden" not in result
        assert "visible.txt" in result

    def test_list_recursive(self, registry, workspace):
        (workspace / "a").mkdir()
        (workspace / "a" / "b.txt").write_text("b", encoding="utf-8")
        result = registry.call("list_directory", {
            "path": str(workspace),
            "recursive": "true",
            "max_depth": 2,
        })
        assert "b.txt" in result

    def test_list_not_found(self, registry, workspace):
        result = registry.call("list_directory", {"path": str(workspace / "nope")})
        assert "Error" in result


# ─── file_info ───────────────────────────────────────────────────────────────


class TestFileInfo:
    def test_file_info(self, registry, workspace):
        f = workspace / "info.py"
        f.write_text("line1\nline2\nline3\n", encoding="utf-8")
        result = registry.call("file_info", {"path": str(f)})
        assert "info.py" in result
        assert "Lines: 3" in result
        assert ".py" in result

    def test_file_info_not_found(self, registry, workspace):
        result = registry.call("file_info", {"path": str(workspace / "nope.txt")})
        assert "Error" in result


# ─── grep_search ─────────────────────────────────────────────────────────────


class TestGrepSearch:
    def test_literal_search(self, registry, workspace):
        (workspace / "a.py").write_text("def hello():\n    pass\n", encoding="utf-8")
        (workspace / "b.py").write_text("def world():\n    pass\n", encoding="utf-8")
        result = registry.call("grep_search", {"pattern": "hello"})
        assert "a.py" in result
        assert "1:" in result  # line number
        assert "b.py" not in result

    def test_regex_search(self, registry, workspace):
        (workspace / "code.py").write_text(
            "val = 123\nval = 456\nname = 'abc'\n", encoding="utf-8"
        )
        result = registry.call("grep_search", {
            "pattern": r"val\s*=\s*\d+",
            "is_regex": "true",
        })
        assert "val = 123" in result
        assert "val = 456" in result
        assert "name" not in result

    def test_case_insensitive(self, registry, workspace):
        (workspace / "data.txt").write_text("Hello\nhello\nHELLO\n", encoding="utf-8")
        result = registry.call("grep_search", {
            "pattern": "hello",
            "case_sensitive": "false",
        })
        assert "3 match" in result

    def test_include_filter(self, registry, workspace):
        (workspace / "code.py").write_text("target_text\n", encoding="utf-8")
        (workspace / "data.txt").write_text("target_text\n", encoding="utf-8")
        result = registry.call("grep_search", {
            "pattern": "target_text",
            "include": "*.py",
        })
        assert "code.py" in result
        assert "data.txt" not in result

    def test_no_matches(self, registry, workspace):
        (workspace / "a.py").write_text("hello\n", encoding="utf-8")
        result = registry.call("grep_search", {"pattern": "nonexistent_xyz"})
        assert "No matches" in result

    def test_search_specific_file(self, registry, workspace):
        f = workspace / "target.py"
        f.write_text("line_one\nline_two\nline_three\n", encoding="utf-8")
        result = registry.call("grep_search", {
            "pattern": "line_two",
            "path": str(f),
        })
        assert "line_two" in result
        assert "1 match" in result


# ─── find_files ──────────────────────────────────────────────────────────────


class TestFindFiles:
    def test_find_by_extension(self, registry, workspace):
        (workspace / "a.py").write_text("", encoding="utf-8")
        (workspace / "b.py").write_text("", encoding="utf-8")
        (workspace / "c.txt").write_text("", encoding="utf-8")
        result = registry.call("find_files", {"name_pattern": "*.py"})
        assert "a.py" in result
        assert "b.py" in result
        assert "c.txt" not in result

    def test_find_by_prefix(self, registry, workspace):
        (workspace / "test_a.py").write_text("", encoding="utf-8")
        (workspace / "test_b.py").write_text("", encoding="utf-8")
        (workspace / "main.py").write_text("", encoding="utf-8")
        result = registry.call("find_files", {"name_pattern": "test_*"})
        assert "test_a.py" in result
        assert "test_b.py" in result
        assert "main.py" not in result

    def test_find_no_results(self, registry, workspace):
        result = registry.call("find_files", {"name_pattern": "*.xyz"})
        assert "No files found" in result


# ─── run_shell ───────────────────────────────────────────────────────────────


class TestRunShell:
    def test_echo_command(self, registry, workspace):
        import sys
        if sys.platform == "win32":
            result = registry.call("run_shell", {"command": "echo hello"})
        else:
            result = registry.call("run_shell", {"command": "echo hello"})
        assert "hello" in result
        assert "exit code: 0" in result

    def test_exit_code_reported(self, registry, workspace):
        import sys
        if sys.platform == "win32":
            result = registry.call("run_shell", {"command": "cmd /c exit 42"})
        else:
            result = registry.call("run_shell", {"command": "sh -c 'exit 42'"})
        assert "exit code: 42" in result

    def test_timeout_handling(self, registry, workspace):
        import sys
        if sys.platform == "win32":
            result = registry.call("run_shell", {"command": "ping -n 10 127.0.0.1", "timeout": 1})
        else:
            result = registry.call("run_shell", {"command": "sleep 10", "timeout": 1})
        assert "timed out" in result.lower()


# ─── environment_info ────────────────────────────────────────────────────────


class TestEnvironmentInfo:
    def test_basic_info(self, registry, workspace):
        result = registry.call("environment_info", {})
        assert "OS:" in result
        assert "Python:" in result
        assert "Workspace:" in result


# ─── workspace sandboxing ────────────────────────────────────────────────────


class TestSandbox:
    def test_path_escape_blocked_read(self, registry, workspace):
        result = registry.call("read_file", {"path": "/etc/passwd"})
        assert "Error" in result

    def test_path_escape_blocked_write(self, registry, workspace):
        result = registry.call("write_file", {"path": "/tmp/evil.txt", "content": "bad"})
        assert "Error" in result

    def test_path_escape_blocked_edit(self, registry, workspace):
        result = registry.call("file_edit", {
            "path": "/etc/hosts",
            "target": "localhost",
            "replacement": "evil",
        })
        assert "Error" in result
