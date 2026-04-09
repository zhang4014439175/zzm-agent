from zzm_agent.core.tool_registry import tool, ToolRegistry


def test_tool_decorator_registers_function():
    """Test that the @tool decorator correctly registers a function in the registry."""
    registry = ToolRegistry()

    @registry.tool(description="加两个数")
    def add(a: int, b: int) -> int:
        return a + b

    assert "add" in registry.tools
    assert registry.tools["add"]["fn"] is add


def test_schema_generation():
    """Test that the registry generates the correct JSON Schema for a registered tool."""
    registry = ToolRegistry()

    @registry.tool(description="加两个数")
    def add(a: int, b: int) -> int:
        return a + b

    schemas = registry.get_schemas()
    assert len(schemas) == 1
    schema = schemas[0]
    assert schema["type"] == "function"
    assert schema["function"]["name"] == "add"
    assert schema["function"]["description"] == "加两个数"
    props = schema["function"]["parameters"]["properties"]
    assert props["a"]["type"] == "integer"
    assert props["b"]["type"] == "integer"
    assert "a" in schema["function"]["parameters"]["required"]
    assert "b" in schema["function"]["parameters"]["required"]


def test_call_tool():
    """Test that calling a tool via the registry works as expected."""
    registry = ToolRegistry()

    @registry.tool(description="加两个数")
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
