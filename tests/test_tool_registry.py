from zzm_agent.core.tool_registry import ToolRegistry, tool


def test_tool_decorator_registers_function():
    """Test that the @tool decorator correctly registers a function in the registry."""
    registry = ToolRegistry()

    @registry.tool(description="add two numbers")
    def add(a: int, b: int) -> int:
        return a + b

    assert "add" in registry.tools
    assert registry.tools["add"]["fn"] is add


def test_schema_generation():
    """Test that the registry generates the correct JSON Schema for a registered tool."""
    registry = ToolRegistry()

    @registry.tool(description="add two numbers")
    def add(a: int, b: int) -> int:
        return a + b

    schemas = registry.get_schemas()
    assert len(schemas) == 1
    schema = schemas[0]
    assert schema["type"] == "function"
    assert schema["function"]["name"] == "add"
    assert schema["function"]["description"] == "add two numbers"
    props = schema["function"]["parameters"]["properties"]
    assert props["a"]["type"] == "integer"
    assert props["b"]["type"] == "integer"
    assert "a" in schema["function"]["parameters"]["required"]
    assert "b" in schema["function"]["parameters"]["required"]


def test_schema_includes_docstring_arg_descriptions():
    registry = ToolRegistry()

    @registry.tool(description="read a file")
    def read(path: str, limit: int = 10) -> str:
        """
        Read part of a file.

        Args:
            path: File path to read.
            limit: Maximum number of lines to return.

        Returns:
            Text content.
        """
        return path

    props = registry.get_schemas()[0]["function"]["parameters"]["properties"]
    assert props["path"]["description"] == "File path to read."
    assert props["limit"]["description"] == "Maximum number of lines to return."


def test_call_tool():
    """Test that calling a tool via the registry works as expected."""
    registry = ToolRegistry()

    @registry.tool(description="add two numbers")
    def add(a: int, b: int) -> int:
        return a + b

    result = registry.call("add", {"a": 3, "b": 4})
    assert result == 7


def test_supported_types():
    """Test that Python types are correctly mapped to JSON Schema types."""
    registry = ToolRegistry()

    @registry.tool(description="test")
    def fn(s: str, i: int, f: float, b: bool) -> str:
        return s

    schemas = registry.get_schemas()
    props = schemas[0]["function"]["parameters"]["properties"]
    assert props["s"]["type"] == "string"
    assert props["i"]["type"] == "integer"
    assert props["f"]["type"] == "number"
    assert props["b"]["type"] == "boolean"


def test_tool_metadata_exposes_risk_level():
    registry = ToolRegistry()

    @registry.tool(description="dangerous operation", risk_level="high")
    def dangerous() -> str:
        return "ok"

    assert registry.get_tool_meta("dangerous") == {
        "name": "dangerous",
        "description": "dangerous operation",
        "risk_level": "high",
    }


def test_reload_plugins_reports_added_removed_and_updated_tools(tmp_path):
    plugin_dir = tmp_path / "plugins"
    plugin_dir.mkdir()
    plugin_file = plugin_dir / "demo.py"
    plugin_file.write_text(
        "from zzm_agent.core.tool_registry import tool\n\n"
        '@tool(description="first version")\n'
        "def alpha(text: str) -> str:\n"
        "    return text\n",
        encoding="utf-8",
    )

    registry = ToolRegistry()
    registry.configure_plugin_dirs([plugin_dir])
    registry.load_configured_plugins()
    assert "alpha" in registry.tools

    plugin_file.write_text(
        "from zzm_agent.core.tool_registry import tool\n\n"
        '@tool(description="second version", risk_level="medium")\n'
        "def alpha(text: str) -> str:\n"
        "    return text\n\n"
        '@tool(description="new tool")\n'
        "def beta() -> str:\n"
        '    return "ok"\n',
        encoding="utf-8",
    )

    changes = registry.reload_plugins()

    assert changes == {
        "added": ["beta"],
        "removed": [],
        "updated": ["alpha"],
    }
    assert set(registry.tools) == {"alpha", "beta"}
    assert registry.get_tool_meta("alpha")["risk_level"] == "medium"

    plugin_file.write_text(
        "from zzm_agent.core.tool_registry import tool\n\n"
        '@tool(description="new tool")\n'
        "def beta() -> str:\n"
        '    return "ok"\n',
        encoding="utf-8",
    )

    changes = registry.reload_plugins()

    assert changes == {
        "added": [],
        "removed": ["alpha"],
        "updated": [],
    }
    assert set(registry.tools) == {"beta"}
