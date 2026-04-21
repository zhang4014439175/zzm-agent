import importlib.util
import hashlib
import inspect
import re
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

_VALID_RISK_LEVELS = {"low", "medium", "high"}
_DOCSTRING_ARG_PATTERN = re.compile(r"^\s*(\w+)\s*:\s*(.+?)\s*$")


class ToolRegistry:
    """
    A registry for managing and invoking tools (functions).
    
    It handles automatic JSON Schema generation from Python function signatures
    and dynamic loading of plugin-based tools.
    """
    
    def __init__(self):
        """Initialize an empty tool registry."""
        self.tools: dict[str, dict] = {}
        self.plugin_dirs: list[Path] = []

    def tool(self, description: str, risk_level: str = "low") -> Callable:
        """
        Decorator to register a function as a tool.
        
        Args:
            description: A human-readable description of what the tool does.
            
        Returns:
            A decorator function that registers the tool and returns the original function.
        """
        normalized_risk = risk_level.strip().lower()
        if normalized_risk not in _VALID_RISK_LEVELS:
            raise ValueError(f"Unsupported risk level: {risk_level}")

        def decorator(fn: Callable) -> Callable:
            # The schema is derived once at registration time so the runtime can
            # hand OpenAI-compatible tool metadata to the model without further
            # reflection during each request.
            sig = inspect.signature(fn)
            arg_descriptions = self._extract_arg_descriptions(fn)
            properties = {}
            required = []
            
            for name, param in sig.parameters.items():
                annotation = param.annotation
                # Map Python types to JSON types, defaulting to "string"
                json_type = _TYPE_MAP.get(annotation, "string")
                properties[name] = {"type": json_type}
                if name in arg_descriptions:
                    properties[name]["description"] = arg_descriptions[name]
                
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
            self.tools[fn.__name__] = {
                "fn": fn,
                "schema": schema,
                "description": description,
                "risk_level": normalized_risk,
            }
            return fn
        return decorator

    def _extract_arg_descriptions(self, fn: Callable) -> dict[str, str]:
        """Extract Args-section parameter descriptions from a tool docstring."""
        doc = inspect.getdoc(fn) or ""
        descriptions: dict[str, str] = {}
        in_args = False
        current_name = ""

        for raw_line in doc.splitlines():
            line = raw_line.rstrip()
            stripped = line.strip()
            if stripped == "Args:":
                in_args = True
                current_name = ""
                continue
            if not in_args:
                continue
            if stripped in {"Returns:", "Raises:", "Examples:"}:
                break
            if not stripped:
                current_name = ""
                continue

            match = _DOCSTRING_ARG_PATTERN.match(line)
            if match:
                current_name = match.group(1)
                descriptions[current_name] = match.group(2).strip()
            elif current_name and raw_line.startswith((" ", "\t")):
                descriptions[current_name] += " " + stripped

        return descriptions

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

    def get_tool_meta(self, name: str) -> dict[str, Any]:
        """Return metadata for one registered tool."""
        if name not in self.tools:
            raise KeyError(f"Tool not found: {name}")
        tool_data = self.tools[name]
        return {
            "name": name,
            "description": tool_data["description"],
            "risk_level": tool_data["risk_level"],
        }

    def configure_plugin_dirs(self, plugin_dirs: list[str | Path]) -> None:
        """Store the plugin directories that should participate in reloads."""
        self.plugin_dirs = [Path(directory).expanduser().resolve() for directory in plugin_dirs]

    def load_configured_plugins(self) -> None:
        """Load every plugin directory previously configured on this registry."""
        # Plugin modules import the module-level `@tool` decorator, so reloads
        # must point that decorator at this registry before executing plugins.
        set_active_registry(self)
        for plugin_dir in self.plugin_dirs:
            self.load_plugin_dir(plugin_dir)

    def reload_plugins(self) -> dict[str, list[str]]:
        """Reload configured plugin directories and report registry changes."""
        previous = self._snapshot_tools()

        reloaded = ToolRegistry()
        reloaded.configure_plugin_dirs(self.plugin_dirs)
        set_active_registry(reloaded)
        reloaded.load_configured_plugins()

        self.tools = reloaded.tools
        set_active_registry(self)

        current = self._snapshot_tools()
        return self._diff_tool_snapshots(previous, current)

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

            # Each plugin is loaded under a synthetic module name so repeated
            # file basenames from different plugin directories do not collide.
            digest = hashlib.sha1(str(py_file.resolve()).encode("utf-8")).hexdigest()[:12]
            module_name = f"_zzm_plugin_{digest}_{py_file.stem}"
            spec = importlib.util.spec_from_file_location(module_name, py_file)
            if spec is None or spec.loader is None:
                continue

            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            spec.loader.exec_module(module)

    def _snapshot_tools(self) -> dict[str, dict[str, str]]:
        """Capture the comparable tool metadata used to diff reload results."""
        snapshot: dict[str, dict[str, str]] = {}
        for name, entry in self.tools.items():
            snapshot[name] = {
                "description": entry["description"],
                "risk_level": entry["risk_level"],
                "schema": repr(entry["schema"]),
            }
        return snapshot

    def _diff_tool_snapshots(
        self,
        previous: dict[str, dict[str, str]],
        current: dict[str, dict[str, str]],
    ) -> dict[str, list[str]]:
        """Return added, removed, and updated tool names after one reload."""
        previous_names = set(previous)
        current_names = set(current)

        added = sorted(current_names - previous_names)
        removed = sorted(previous_names - current_names)
        updated = sorted(
            name
            for name in previous_names & current_names
            if previous[name] != current[name]
        )
        return {
            "added": added,
            "removed": removed,
            "updated": updated,
        }

# Global registry instance management for simplified decorator usage
_active_registry: ToolRegistry | None = None


def set_active_registry(registry: ToolRegistry) -> None:
    """Set the global active tool registry."""
    global _active_registry
    # Plugins import the module-level `@tool` decorator, so startup must point
    # that decorator at the same registry instance the agent loop will query.
    _active_registry = registry


def tool(description: str, risk_level: str = "low") -> Callable:
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
    return _active_registry.tool(description, risk_level=risk_level)
