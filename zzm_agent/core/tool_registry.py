import importlib
import importlib.util
import inspect
import sys
from pathlib import Path
from typing import Any, Callable

_TYPE_MAP = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
    list: "array",
    dict: "object",
}


class ToolRegistry:
    def __init__(self):
        self.tools: dict[str, dict] = {}

    def tool(self, description: str) -> Callable:
        def decorator(fn: Callable) -> Callable:
            sig = inspect.signature(fn)
            properties = {}
            required = []
            for name, param in sig.parameters.items():
                annotation = param.annotation
                json_type = _TYPE_MAP.get(annotation, "string")
                properties[name] = {"type": json_type}
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
            self.tools[fn.__name__] = {"fn": fn, "schema": schema}
            return fn
        return decorator

    def get_schemas(self) -> list[dict]:
        return [v["schema"] for v in self.tools.values()]

    def call(self, name: str, arguments: dict[str, Any]) -> Any:
        if name not in self.tools:
            raise KeyError(f"Tool not found: {name}")
        return self.tools[name]["fn"](**arguments)

    def load_plugin_dir(self, directory: str | Path) -> None:
        path = Path(directory).expanduser().resolve()
        if not path.exists():
            return
        for py_file in sorted(path.glob("*.py")):
            if py_file.name.startswith("_"):
                continue
            module_name = f"_zzm_plugin_{py_file.stem}"
            spec = importlib.util.spec_from_file_location(module_name, py_file)
            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            spec.loader.exec_module(module)

_active_registry: ToolRegistry | None = None


def set_active_registry(registry: ToolRegistry) -> None:
    global _active_registry
    _active_registry = registry


def tool(description: str) -> Callable:
    if _active_registry is None:
        raise RuntimeError("Active ToolRegistry is not set")
    return _active_registry.tool(description)
