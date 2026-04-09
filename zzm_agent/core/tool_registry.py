import importlib
import importlib.util
import inspect
import sys
from pathlib import Path
from typing import Any, Callable

# Mapping from Python types to JSON Schema types
_TYPE_MAP = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
    list: "array",
    dict: "object",
}


class ToolRegistry:
    """
    A registry for managing and invoking tools (functions).
    
    It handles automatic JSON Schema generation from Python function signatures
    and dynamic loading of plugin-based tools.
    """
    
    def __init__(self):
        """Initialize an empty tool registry."""
        self.tools: dict[str, dict] = {}

    def tool(self, description: str) -> Callable:
        """
        Decorator to register a function as a tool.
        
        Args:
            description: A human-readable description of what the tool does.
            
        Returns:
            A decorator function that registers the tool and returns the original function.
        """
        def decorator(fn: Callable) -> Callable:
            # Introspect the function signature to build the JSON Schema
            sig = inspect.signature(fn)
            properties = {}
            required = []
            
            for name, param in sig.parameters.items():
                annotation = param.annotation
                # Map Python types to JSON types, defaulting to "string"
                json_type = _TYPE_MAP.get(annotation, "string")
                properties[name] = {"type": json_type}
                
                # If there's no default value, the parameter is required
                if param.default is inspect.Parameter.empty:
                    required.append(name)

            schema = {
                "type": "function",
                "function": {
                    "name": fn.__name__,
                    "description": description,
                    "parameters": {
                        "type": "object",
                        "properties": properties,
                        "required": required,
                    },
                },
            }
            # Store both the function reference and its generated schema
            self.tools[fn.__name__] = {"fn": fn, "schema": schema}
            return fn
        return decorator

    def get_schemas(self) -> list[dict]:
        """
        Retrieve the JSON Schemas for all registered tools.
        
        Returns:
            A list of tool schemas compatible with LLM function calling.
        """
        return [v["schema"] for v in self.tools.values()]

    def call(self, name: str, arguments: dict[str, Any]) -> Any:
        """
        Invoke a registered tool by name with the provided arguments.
        
        Args:
            name: The name of the tool to call.
            arguments: A dictionary of keyword arguments for the function.
            
        Returns:
            The result of the tool execution.
            
        Raises:
            KeyError: If the tool name is not found in the registry.
        """
        if name not in self.tools:
            raise KeyError(f"Tool not found: {name}")
        return self.tools[name]["fn"](**arguments)

    def load_plugin_dir(self, directory: str | Path) -> None:
        """
        Dynamically load all Python modules from a directory as plugins.
        
        Args:
            directory: The path to the directory containing plugin files (*.py).
        """
        path = Path(directory).expanduser().resolve()
        if not path.exists():
            return
            
        for py_file in sorted(path.glob("*.py")):
            # Skip hidden files and __init__.py
            if py_file.name.startswith("_"):
                continue
            
            # Create a unique module name to avoid collisions
            module_name = f"_zzm_plugin_{py_file.stem}"
            spec = importlib.util.spec_from_file_location(module_name, py_file)
            if spec is None or spec.loader is None:
                continue
                
            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            spec.loader.exec_module(module)

# Global registry instance management for simplified decorator usage
_active_registry: ToolRegistry | None = None


def set_active_registry(registry: ToolRegistry) -> None:
    """Set the global active tool registry."""
    global _active_registry
    _active_registry = registry


def tool(description: str) -> Callable:
    """
    Convenience decorator that uses the global active ToolRegistry.
    
    Args:
        description: Description of the tool.
        
    Returns:
        The decorator from the active registry.
        
    Raises:
        RuntimeError: If no active registry has been set.
    """
    if _active_registry is None:
        raise RuntimeError("Active ToolRegistry is not set")
    return _active_registry.tool(description)
