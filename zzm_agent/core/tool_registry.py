import importlib.util
import hashlib
import inspect
import json
import re
import sys
import traceback
from dataclasses import dataclass, field
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator

from zzm_agent.core.plugin import BasePlugin, PluginContext

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


class ToolArgumentValidationError(TypeError):
    """Raised before tool code runs when arguments do not match its schema."""

    def __init__(self, tool_name: str, issues: list[str]):
        self.tool_name = tool_name
        self.issues = list(issues)
        super().__init__(f"Invalid arguments for tool {tool_name}: " + "; ".join(issues))


@dataclass
class PluginLoadError:
    """A plugin failure captured without aborting registry startup."""

    plugin: str
    path: str
    error_type: str
    message: str
    traceback: str = ""


@dataclass
class _RegistrationContext:
    namespace: str = ""
    plugin_name: str = ""
    plugin_version: str = ""
    group: str = ""
    default_risk_level: str | None = None
    config: dict[str, Any] = field(default_factory=dict)


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
        self.plugin_config: dict[str, Any] = {}
        self.plugin_errors: list[PluginLoadError] = []
        self._plugin_instances: list[BasePlugin] = []
        self._registration_context_stack: list[_RegistrationContext] = []

    def tool(
        self,
        description: str,
        risk_level: str = "low",
        group: str = "",
        examples: list[str] | None = None,
    ) -> Callable:
        """
        Decorator to register a function as a tool.
        
        Args:
            description: A human-readable description of what the tool does.
            risk_level: Risk level used by confirmation policy.
            group: Optional display group used in prompt and tool listings.
            examples: Optional short usage examples for prompt guidance.
            
        Returns:
            A decorator function that registers the tool and returns the original function.
        """
        context = self._current_registration_context()
        risk_source = context.default_risk_level or risk_level
        normalized_risk = risk_source.strip().lower()
        if normalized_risk not in _VALID_RISK_LEVELS:
            raise ValueError(f"Unsupported risk level: {risk_source}")

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

            registered_name = self._qualified_tool_name(fn.__name__, context.namespace)
            schema = {
                "type": "function",
                "function": {
                    "name": registered_name,
                    "description": description,
                    "parameters": {
                        "type": "object",
                        "properties": properties,
                        "required": required,
                        "additionalProperties": False,
                    },
                },
            }
            # Store both the function reference and its generated schema
            self.tools[registered_name] = {
                "fn": fn,
                "schema": schema,
                "description": description,
                "risk_level": normalized_risk,
                "plugin_name": context.plugin_name,
                "plugin_version": context.plugin_version,
                "namespace": context.namespace,
                "group": group or context.group,
                "examples": list(examples or []),
            }
            return fn
        return decorator

    def _current_registration_context(self) -> _RegistrationContext:
        """Return plugin metadata for tools being registered right now."""
        if not self._registration_context_stack:
            return _RegistrationContext()
        return self._registration_context_stack[-1]

    def _qualified_tool_name(self, name: str, namespace: str) -> str:
        """Apply an optional plugin namespace to a tool name."""
        if not namespace:
            return name
        if name.startswith(f"{namespace}."):
            return name
        return f"{namespace}.{name}"

    @contextmanager
    def _registration_context(self, context: _RegistrationContext) -> Iterator[None]:
        """Temporarily attach plugin metadata to tool registrations."""
        self._registration_context_stack.append(context)
        try:
            yield
        finally:
            self._registration_context_stack.pop()

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
        validated = self.validate_arguments(name, arguments)
        return self.tools[name]["fn"](**validated)

    def validate_arguments(self, name: str, arguments: Any) -> dict[str, Any]:
        """Validate one call against the registered JSON schema without coercion."""
        if name not in self.tools:
            raise KeyError(f"Tool not found: {name}")
        if not isinstance(arguments, dict):
            raise ToolArgumentValidationError(name, ["arguments must be a JSON object"])

        parameters = self.tools[name]["schema"]["function"]["parameters"]
        properties = dict(parameters.get("properties") or {})
        required = set(parameters.get("required") or [])
        issues: list[str] = []

        missing = sorted(required - set(arguments))
        if missing:
            issues.append("missing required parameter(s): " + ", ".join(missing))

        unknown = sorted(set(arguments) - set(properties))
        if unknown and parameters.get("additionalProperties") is False:
            issues.append("unknown parameter(s): " + ", ".join(unknown))

        for key in sorted(set(arguments) & set(properties)):
            expected = properties[key].get("type", "string")
            if not self._matches_json_type(arguments[key], expected):
                actual = self._json_type_name(arguments[key])
                issues.append(f"parameter {key!r} must be {expected}, got {actual}")

        if issues:
            raise ToolArgumentValidationError(name, issues)
        return dict(arguments)

    @staticmethod
    def _matches_json_type(value: Any, expected: str) -> bool:
        if expected == "string":
            return isinstance(value, str)
        if expected == "integer":
            return isinstance(value, int) and not isinstance(value, bool)
        if expected == "number":
            return isinstance(value, (int, float)) and not isinstance(value, bool)
        if expected == "boolean":
            return isinstance(value, bool)
        if expected == "array":
            return isinstance(value, list)
        if expected == "object":
            return isinstance(value, dict)
        return True

    @staticmethod
    def _json_type_name(value: Any) -> str:
        if value is None:
            return "null"
        if isinstance(value, bool):
            return "boolean"
        if isinstance(value, str):
            return "string"
        if isinstance(value, int):
            return "integer"
        if isinstance(value, float):
            return "number"
        if isinstance(value, list):
            return "array"
        if isinstance(value, dict):
            return "object"
        return type(value).__name__

    def get_tool_meta(self, name: str) -> dict[str, Any]:
        """Return metadata for one registered tool."""
        if name not in self.tools:
            raise KeyError(f"Tool not found: {name}")
        tool_data = self.tools[name]
        return {
            "name": name,
            "description": tool_data["description"],
            "risk_level": tool_data["risk_level"],
            "group": tool_data.get("group", ""),
            "examples": list(tool_data.get("examples", [])),
        }

    def configure_plugin_dirs(
        self,
        plugin_dirs: list[str | Path],
        plugin_config: dict[str, Any] | None = None,
    ) -> None:
        """Store the plugin directories that should participate in reloads."""
        self.plugin_dirs = [Path(directory).expanduser().resolve() for directory in plugin_dirs]
        self.plugin_config = plugin_config or {}

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
        reloaded.configure_plugin_dirs(self.plugin_dirs, plugin_config=self.plugin_config)
        set_active_registry(reloaded)
        reloaded.load_configured_plugins()

        self.shutdown_plugins()
        self.tools = reloaded.tools
        self.plugin_errors = reloaded.plugin_errors
        self._plugin_instances = reloaded._plugin_instances
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

        manifest_path = path / "plugin.json"
        if manifest_path.exists():
            self._load_manifest_plugin(path, manifest_path)
            return

        for py_file in sorted(path.glob("*.py")):
            # Skip hidden files and __init__.py
            if py_file.name.startswith("_"):
                continue
            self._load_legacy_module_plugin(py_file)

    def shutdown_plugins(self) -> None:
        """Call shutdown hooks for lifecycle-aware plugin instances."""
        for plugin in reversed(self._plugin_instances):
            try:
                plugin.shutdown()
            except Exception as exc:
                self._record_plugin_error(
                    plugin=getattr(plugin, "name", plugin.__class__.__name__),
                    path="<shutdown>",
                    exc=exc,
                )
        self._plugin_instances = []

    def get_plugin_errors(self) -> list[dict[str, str]]:
        """Return plugin load errors as serializable dictionaries."""
        return [
            {
                "plugin": error.plugin,
                "path": error.path,
                "error_type": error.error_type,
                "message": error.message,
            }
            for error in self.plugin_errors
        ]

    def _load_legacy_module_plugin(self, py_file: Path) -> None:
        """Load a decorator-only plugin module, isolating any import failure."""
        context = _RegistrationContext(plugin_name=py_file.stem)
        previous_tools = set(self.tools)
        try:
            with self._registration_context(context):
                self._exec_plugin_module(py_file)
        except Exception as exc:
            self._rollback_partial_plugin_tools(previous_tools)
            self._record_plugin_error(py_file.stem, str(py_file), exc)

    def _load_manifest_plugin(self, root: Path, manifest_path: Path) -> None:
        """Load one manifest-backed plugin package."""
        previous_tools = set(self.tools)
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if not isinstance(manifest, dict):
                raise ValueError("plugin.json must contain a JSON object")

            plugin_name = str(manifest.get("name") or root.name)
            version = str(manifest.get("version") or "0.0.0")
            entry = str(manifest.get("entry") or "__init__.py")
            namespace = str(manifest.get("namespace") or "")
            group = str(manifest.get("group") or "")
            default_risk_level = manifest.get("risk_level")
            if default_risk_level is not None:
                default_risk_level = str(default_risk_level)
            config_key = str(manifest.get("config_key") or plugin_name)
            config = self.plugin_config.get(config_key, {})
            if not isinstance(config, dict):
                raise ValueError(f"Plugin config for {config_key!r} must be a mapping")

            entry_path = (root / entry).resolve()
            if not entry_path.is_relative_to(root.resolve()):
                raise ValueError("Plugin entry must stay inside the plugin directory")
            if not entry_path.exists():
                raise FileNotFoundError(f"Plugin entry not found: {entry}")

            reg_context = _RegistrationContext(
                namespace=namespace,
                plugin_name=plugin_name,
                plugin_version=version,
                group=group,
                default_risk_level=default_risk_level,
                config=config,
            )
            with self._registration_context(reg_context):
                module = self._exec_plugin_module(entry_path)
                plugin = self._get_module_plugin(module)
                if plugin is None:
                    return

                context = PluginContext(
                    name=plugin_name,
                    version=version,
                    root=root,
                    config=config,
                    manifest=manifest,
                    namespace=namespace,
                    group=group,
                    default_risk_level=default_risk_level,
                )
                plugin.initialize(context)
                plugin.register_tools(self)
                self._plugin_instances.append(plugin)
        except Exception as exc:
            self._rollback_partial_plugin_tools(previous_tools)
            self._record_plugin_error(root.name, str(manifest_path), exc)

    def _rollback_partial_plugin_tools(self, previous_tools: set[str]) -> None:
        """Remove tools that were registered by a plugin that failed to load."""
        for name in set(self.tools) - previous_tools:
            self.tools.pop(name, None)

    def _exec_plugin_module(self, py_file: Path) -> Any:
        """Execute a plugin module under a unique synthetic module name."""
        digest = hashlib.sha1(str(py_file.resolve()).encode("utf-8")).hexdigest()[:12]
        module_name = f"_zzm_plugin_{digest}_{py_file.stem}"
        spec = importlib.util.spec_from_file_location(module_name, py_file)
        if spec is None or spec.loader is None:
            raise ImportError(f"Unable to load plugin module: {py_file}")

        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        return module

    def _get_module_plugin(self, module: Any) -> BasePlugin | None:
        """Return a lifecycle plugin instance exported by a module, if any."""
        plugin_factory = getattr(module, "get_plugin", None)
        plugin = plugin_factory() if callable(plugin_factory) else getattr(module, "plugin", None)
        if plugin is None:
            return None
        if not isinstance(plugin, BasePlugin):
            raise TypeError("plugin or get_plugin() must return a BasePlugin instance")
        return plugin

    def _record_plugin_error(self, plugin: str, path: str, exc: Exception) -> None:
        """Record a plugin failure while allowing other plugins to load."""
        self.plugin_errors.append(
            PluginLoadError(
                plugin=plugin,
                path=path,
                error_type=exc.__class__.__name__,
                message=str(exc),
                traceback=traceback.format_exc(),
            )
        )

    def _snapshot_tools(self) -> dict[str, dict[str, str]]:
        """Capture the comparable tool metadata used to diff reload results."""
        snapshot: dict[str, dict[str, str]] = {}
        for name, entry in self.tools.items():
            snapshot[name] = {
                "description": entry["description"],
                "risk_level": entry["risk_level"],
                "schema": repr(entry["schema"]),
                "plugin_name": entry.get("plugin_name", ""),
                "plugin_version": entry.get("plugin_version", ""),
                "namespace": entry.get("namespace", ""),
                "group": entry.get("group", ""),
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
