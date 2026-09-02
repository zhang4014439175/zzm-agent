from __future__ import annotations

from pathlib import Path

from zzm_agent.cli_support.ui.completion import SlashCommandCompleter
from zzm_agent.core.context_preparation import ContextPreparationService
from zzm_agent.core.state.turn import TurnState
from zzm_agent.core.tool_exposure import ToolExposureManager
from zzm_agent.core.tool_registry import ToolRegistry
from zzm_agent.skills import SkillManager


class _Store:
    """提供上下文准备测试所需的最小内存存储。"""

    max_context_tokens = 4000

    def load_history(self) -> list[dict]:
        """返回空历史，避免无关消息影响 Schema 预算断言。"""
        return []

    def build_turn_messages(
        self,
        system_prompt: str,
        user_input: str,
        memory_limit: int | None = None,
        *,
        tool_schema_tokens: int = 0,
        output_reserve_tokens: int = 0,
        runtime_instruction_tokens: int = 0,
        prompt_cache_strategy: str = "stable_prefix",
    ) -> tuple[list[dict], dict]:
        """返回稳定消息和空诊断容器，供上下文服务补充工具观测数据。"""
        return (
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_input},
            ],
            {"budget_breakdown": {}, "context_sources": []},
        )


def _registry() -> ToolRegistry:
    """构造同时包含常驻本地工具和两个延迟 MCP 工具的注册表。"""
    registry = ToolRegistry()

    @registry.tool("Read a local text file")
    def read_file(path: str) -> str:
        """返回测试路径，不访问真实文件。"""
        return path

    registry.register_external_tool(
        name="mcp.crm.greet_customer",
        description="Greet a CRM customer by name",
        parameters={
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        },
        handler=lambda name: f"hello {name}",
        source="mcp",
        server_name="crm",
        lazy_schema=True,
    )
    registry.register_external_tool(
        name="mcp.files.archive_document",
        description="Archive a remote document",
        parameters={"type": "object", "properties": {}},
        handler=lambda: "archived",
        source="mcp",
        server_name="files",
        lazy_schema=True,
    )
    return registry


def _schema_names(schemas: list[dict]) -> set[str]:
    """从 OpenAI 工具 Schema 列表提取函数名集合。"""
    return {str(item["function"]["name"]) for item in schemas}


def test_lazy_mcp_schemas_are_hidden_until_task_or_explicit_prefix_selects_them() -> None:
    """验证 MCP Schema 默认隐藏，并可由任务语义或独立前缀精确启用。"""
    manager = ToolExposureManager(_registry())

    manager.prepare_for_turn("summarize the answer")
    baseline = _schema_names(manager.get_schemas())
    assert "read_file" in baseline
    assert "tool_search" in baseline
    assert "mcp.crm.greet_customer" not in baseline
    assert "mcp.files.archive_document" not in baseline

    manager.prepare_for_turn("please greet the CRM customer")
    assert "mcp.crm.greet_customer" in _schema_names(manager.get_schemas())
    assert manager.state.activation_reasons["mcp.crm.greet_customer"].startswith("task:")

    manager.prepare_for_turn("use @mcp:files.archive_document now")
    selected = _schema_names(manager.get_schemas())
    assert "mcp.files.archive_document" in selected
    assert "mcp.crm.greet_customer" not in selected


def test_skill_allowed_tools_and_model_tool_search_enable_only_matching_candidates() -> None:
    """验证 Skill 声明与模型 Tool Search 只扩大命中的工具集合。"""
    registry = _registry()
    manager = ToolExposureManager(registry)

    manager.prepare_for_turn("prepare records", allowed_tools=["mcp.crm.greet_customer"])
    assert "mcp.crm.greet_customer" in _schema_names(manager.get_schemas())
    assert manager.state.activation_reasons["mcp.crm.greet_customer"] == "skill:allowed_tools"

    manager.prepare_for_turn("perform a remote operation")
    result = registry.call("tool_search", {"query": "archive doc", "source": "mcp"})
    enabled = _schema_names(manager.get_schemas())
    assert result["enabled"] == ["mcp.files.archive_document"]
    assert "mcp.files.archive_document" in enabled
    assert "mcp.crm.greet_customer" not in enabled
    assert registry.get_tool_meta("tool_search")["risk_level"] == "low"
    assert registry.get_tool_meta("mcp.files.archive_document")["risk_level"] == "medium"

    manager.prepare_for_turn("[CONTINUE_TASK_FROM_CHECKPOINT]\ncontinue the same task")
    assert "mcp.files.archive_document" in _schema_names(manager.get_schemas())
    assert manager.state.activation_reasons["mcp.files.archive_document"] == "stage:continuation"


def test_mcp_completion_uses_separate_prefix_and_does_not_mix_with_skills(
    tmp_path: Path,
) -> None:
    """验证会话框用 ``@mcp:`` 搜索工具，而 ``$`` 菜单仍只展示 Skill。"""
    pytest = __import__("pytest")
    document_module = pytest.importorskip("prompt_toolkit.document")
    completion_module = pytest.importorskip("prompt_toolkit.completion")
    skill_dir = tmp_path / "review"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\nname: review\ndescription: Review code\n---\nReview carefully.\n",
        encoding="utf-8",
    )
    skills = SkillManager([tmp_path])
    exposure = ToolExposureManager(_registry())
    completer = SlashCommandCompleter({"/help": "Show help"}, skills, exposure)

    mcp_items = list(completer.get_completions(
        document_module.Document("调用 @mcp:cg"),
        completion_module.CompleteEvent(),
    ))
    skill_items = list(completer.get_completions(
        document_module.Document("使用 $r"),
        completion_module.CompleteEvent(),
    ))

    assert [item.text for item in mcp_items] == ["@mcp:crm.greet_customer"]
    assert "MCP · crm" in str(mcp_items[0].display_meta)
    assert [item.text for item in skill_items] == ["$review"]


def test_context_and_turn_state_report_schema_savings_and_activation_reasons() -> None:
    """验证上下文与 Turn 快照可解释暴露集合、原因和节省的 Schema 成本。"""
    registry = _registry()
    exposure = ToolExposureManager(registry, token_counter=len)
    service = ContextPreparationService(
        store=_Store(),
        registry=registry,
        system_prompt="base",
        prompt_manager=None,
        token_counter=len,
        memory_injection_limit=0,
        max_output_tokens=100,
        supports_prompt_cache=False,
        tool_exposure_manager=exposure,
    )

    prepared = service.prepare("use @mcp:crm.greet_customer")
    record = prepared.tool_exposure_state.to_record()
    turn = TurnState(user_input="task", tool_exposure_state=record)
    restored = TurnState.from_record(turn.to_record())

    assert prepared.compression["tool_exposure_state"] == record
    assert record["hidden"] == ["mcp.files.archive_document"]
    assert record["schema_tokens_saved"] > 0
    assert restored.tool_exposure_state == record
