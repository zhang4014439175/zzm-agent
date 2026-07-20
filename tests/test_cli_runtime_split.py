from __future__ import annotations

import ast
from pathlib import Path

from zzm_agent.cli_support import bootstrap, execution, repl, runtime


ROOT = Path(__file__).resolve().parents[1]


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def test_cli_runtime_responsibilities_have_dedicated_modules() -> None:
    assert bootstrap.build_runtime.__module__ == "zzm_agent.cli_support.bootstrap"
    assert execution.run_exec.__module__ == "zzm_agent.cli_support.execution"
    assert repl.run_repl.__module__ == "zzm_agent.cli_support.repl"


def test_execution_and_repl_do_not_coordinate_agent_loop_directly() -> None:
    for name in ("execution.py", "repl.py"):
        imports = _imported_modules(ROOT / "zzm_agent" / "cli_support" / name)
        assert "zzm_agent.core.agent_loop" not in imports
    assert "zzm_agent.core.query_engine" not in _imported_modules(
        ROOT / "zzm_agent" / "cli_support" / "execution.py"
    )


def test_legacy_runtime_module_remains_a_compatibility_facade() -> None:
    assert runtime.parse_args is bootstrap.parse_args
    assert runtime.run_exec is execution.run_exec
    assert runtime.build_runtime is bootstrap.build_runtime

