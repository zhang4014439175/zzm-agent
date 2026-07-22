from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from zzm_agent.core.tool_registry import ToolRegistry
from zzm_agent.evolution.optimizer import EvolutionOptimizer
from zzm_agent.memory.store import MemoryStore


@dataclass(slots=True)
class CommandContext:
    """Dependencies injected into slash-command routing."""

    registry: ToolRegistry
    store: MemoryStore
    optimizer: EvolutionOptimizer
    console: Any
    runtime: dict[str, Any] | None = None

    def dependencies(
        self,
    ) -> tuple[
        ToolRegistry,
        MemoryStore,
        EvolutionOptimizer,
        Any,
        dict[str, Any] | None,
    ]:
        """Return dependencies in the legacy handler argument order."""
        return self.registry, self.store, self.optimizer, self.console, self.runtime
