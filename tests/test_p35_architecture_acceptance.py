from __future__ import annotations

import ast
from pathlib import Path

from zzm_agent.cli_support import bootstrap, execution, repl


ROOT = Path(__file__).resolve().parents[1]


def _imports(relative_path: str) -> set[str]:
    path = ROOT / relative_path
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def test_cli_execution_boundaries_do_not_import_agent_loop() -> None:
    assert "zzm_agent.core.agent_loop" not in _imports(
        "zzm_agent/cli_support/execution.py"
    )
    assert "zzm_agent.core.agent_loop" not in _imports(
        "zzm_agent/cli_support/repl.py"
    )
    assert bootstrap.build_runtime.__module__ == "zzm_agent.cli_support.bootstrap"
    assert repl.run_repl.__module__ == "zzm_agent.cli_support.repl"


def test_workspace_side_effect_entrypoints_use_runtime_adapters() -> None:
    assert "zzm_agent.workspace.filesystem" in _imports(
        "zzm_agent/plugins/file_ops.py"
    )
    assert "zzm_agent.workspace.process" in _imports(
        "zzm_agent/plugins/shell.py"
    )
    assert "zzm_agent.workspace.git" in _imports(
        "zzm_agent/cli_support/git_workflow.py"
    )


def test_runtime_event_and_state_compatibility_facades_remain_available() -> None:
    from zzm_agent.core.runtime_records import EventBus, RuntimeEvent
    from zzm_agent.runtime.events import EventBus as SplitEventBus
    from zzm_agent.runtime.events import RuntimeEvent as SplitRuntimeEvent

    assert EventBus is SplitEventBus
    assert RuntimeEvent is SplitRuntimeEvent
