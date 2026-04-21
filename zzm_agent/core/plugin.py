from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from zzm_agent.core.tool_registry import ToolRegistry


@dataclass(frozen=True)
class PluginContext:
    """Runtime context passed to lifecycle-aware plugins."""

    name: str
    version: str
    root: Path
    config: dict[str, Any] = field(default_factory=dict)
    manifest: dict[str, Any] = field(default_factory=dict)
    namespace: str = ""
    group: str = ""
    default_risk_level: str | None = None


class BasePlugin:
    """Base class for plugins that need explicit lifecycle hooks."""

    name: str = ""
    version: str = "0.0.0"

    def initialize(self, context: PluginContext) -> None:
        """Prepare plugin resources before tool registration."""

    def register_tools(self, registry: "ToolRegistry") -> None:
        """Register plugin tools in the provided registry."""

    def shutdown(self) -> None:
        """Release plugin resources."""
