import pytest

from zzm_agent.core.tool_registry import (
    ToolArgumentValidationError,
    ToolRegistry,
    tool,
)


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


def test_invalid_arguments_never_enter_tool_function():
    registry = ToolRegistry()
    calls = []

    @registry.tool(description="write a value", risk_level="high")
    def write_value(path: str, count: int = 1) -> str:
        calls.append((path, count))
        return "ok"

    with pytest.raises(ToolArgumentValidationError, match="missing required.*path"):
        registry.call("write_value", {"count": 1})
    with pytest.raises(ToolArgumentValidationError, match="must be integer"):
        registry.call("write_value", {"path": "x", "count": "1"})
    with pytest.raises(ToolArgumentValidationError, match="unknown parameter.*force"):
        registry.call("write_value", {"path": "x", "force": True})

    assert calls == []


def test_schema_rejects_additional_properties_and_validation_does_not_coerce():
    registry = ToolRegistry()

    @registry.tool(description="typed arguments")
    def typed(enabled: bool, ratio: float, items: list, metadata: dict) -> str:
        return "ok"

    parameters = registry.get_schemas()[0]["function"]["parameters"]
    assert parameters["additionalProperties"] is False
    assert registry.validate_arguments(
        "typed",
        {"enabled": True, "ratio": 2, "items": [], "metadata": {}},
    )["ratio"] == 2
    with pytest.raises(ToolArgumentValidationError, match="boolean"):
        registry.validate_arguments(
            "typed",
            {"enabled": 1, "ratio": 2, "items": [], "metadata": {}},
        )


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
        "group": "",
        "examples": [],
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


def test_plugin_load_failure_is_isolated(tmp_path):
    plugin_dir = tmp_path / "plugins"
    plugin_dir.mkdir()
    (plugin_dir / "bad.py").write_text(
        "raise RuntimeError('boom during import')\n",
        encoding="utf-8",
    )
    (plugin_dir / "good.py").write_text(
        "from zzm_agent.core.tool_registry import tool\n\n"
        '@tool(description="good tool")\n'
        "def good() -> str:\n"
        '    return "ok"\n',
        encoding="utf-8",
    )

    registry = ToolRegistry()
    registry.configure_plugin_dirs([plugin_dir])
    registry.load_configured_plugins()

    assert registry.call("good", {}) == "ok"
    errors = registry.get_plugin_errors()
    assert len(errors) == 1
    assert errors[0]["plugin"] == "bad"
    assert errors[0]["error_type"] == "RuntimeError"


def test_failed_plugin_does_not_leave_partially_registered_tools(tmp_path):
    plugin_dir = tmp_path / "plugins"
    plugin_dir.mkdir()
    (plugin_dir / "partial.py").write_text(
        "from zzm_agent.core.tool_registry import tool\n\n"
        '@tool(description="partial tool")\n'
        "def partial() -> str:\n"
        '    return "bad"\n\n'
        "raise RuntimeError('failed after registration')\n",
        encoding="utf-8",
    )
    (plugin_dir / "good.py").write_text(
        "from zzm_agent.core.tool_registry import tool\n\n"
        '@tool(description="good tool")\n'
        "def good() -> str:\n"
        '    return "ok"\n',
        encoding="utf-8",
    )

    registry = ToolRegistry()
    registry.configure_plugin_dirs([plugin_dir])
    registry.load_configured_plugins()

    assert set(registry.tools) == {"good"}
    assert registry.call("good", {}) == "ok"


def test_manifest_plugin_supports_namespace_config_and_lifecycle(tmp_path):
    plugin_dir = tmp_path / "demo_plugin"
    plugin_dir.mkdir()
    (plugin_dir / "plugin.json").write_text(
        "{"
        '"name": "demo",'
        '"version": "1.2.3",'
        '"entry": "main.py",'
        '"namespace": "demo",'
        '"group": "examples",'
        '"risk_level": "medium",'
        '"config_key": "demo"'
        "}",
        encoding="utf-8",
    )
    (plugin_dir / "main.py").write_text(
        "from pathlib import Path\n"
        "from zzm_agent.core.plugin import BasePlugin\n\n"
        "class DemoPlugin(BasePlugin):\n"
        "    def initialize(self, context):\n"
        "        self.root = context.root\n"
        "        self.suffix = context.config['suffix']\n"
        "        (self.root / 'initialized.txt').write_text(context.name, encoding='utf-8')\n\n"
        "    def register_tools(self, registry):\n"
        "        suffix = self.suffix\n"
        "        @registry.tool(description='echo with configured suffix')\n"
        "        def echo(text: str) -> str:\n"
        "            return text + suffix\n\n"
        "    def shutdown(self):\n"
        "        (self.root / 'shutdown.txt').write_text('closed', encoding='utf-8')\n\n"
        "plugin = DemoPlugin()\n",
        encoding="utf-8",
    )

    registry = ToolRegistry()
    registry.configure_plugin_dirs([plugin_dir], plugin_config={"demo": {"suffix": "!"}})
    registry.load_configured_plugins()

    assert (plugin_dir / "initialized.txt").read_text(encoding="utf-8") == "demo"
    assert registry.call("demo.echo", {"text": "hi"}) == "hi!"
    assert registry.get_tool_meta("demo.echo")["risk_level"] == "medium"

    registry.shutdown_plugins()

    assert (plugin_dir / "shutdown.txt").read_text(encoding="utf-8") == "closed"
