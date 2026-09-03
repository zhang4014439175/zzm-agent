from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from zzm_agent.core.tool_registry import ToolRegistry


@dataclass(frozen=True)
class PluginContext:
    """传给具备生命周期钩子的插件的只读运行时上下文。

    ``permissions`` 仅复述 Manifest 中的能力声明，方便插件和诊断层展示；它不会
    授予文件、网络、进程或密钥访问权，实际操作仍由宿主的工具权限链路控制。
    """

    name: str
    version: str
    root: Path
    config: dict[str, Any] = field(default_factory=dict)
    manifest: dict[str, Any] = field(default_factory=dict)
    namespace: str = ""
    group: str = ""
    default_risk_level: str | None = None
    permissions: dict[str, Any] = field(default_factory=dict)


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
