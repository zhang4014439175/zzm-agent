from __future__ import annotations

from pathlib import Path

from zzm_agent.core.context_preparation import ContextPreparationService
from zzm_agent.core.state.turn import TurnState
from zzm_agent.cli_support.commands.router import handle_slash
from zzm_agent.skills import SkillManager


class _Store:
    max_context_tokens = 2000

    def load_history(self) -> list[dict]:
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
        return (
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_input},
            ],
            {"budget_breakdown": {}, "context_sources": []},
        )


class _Registry:
    def get_schemas(self) -> list[dict]:
        return []


class _Console:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def print(self, value: object = "") -> None:
        self.lines.append(str(value))


def _write_skill(
    root: Path,
    name: str,
    *,
    description: str = "Review Python changes",
    triggers: str = "[review python]",
    enabled: bool = True,
    resources: str = "[]",
    body: str = "Follow the review checklist.",
) -> Path:
    skill_dir = root / name
    skill_dir.mkdir(parents=True)
    skill_file = skill_dir / "SKILL.md"
    skill_file.write_text(
        "---\n"
        f"name: {name}\n"
        f"description: {description}\n"
        f"triggers: {triggers}\n"
        f"enabled: {str(enabled).lower()}\n"
        f"resources: {resources}\n"
        "allowed_tools: [read_file, search]\n"
        "scripts: [scripts/check.py]\n"
        "---\n"
        f"{body}\n",
        encoding="utf-8",
    )
    return skill_file


def test_skill_discovery_reads_metadata_before_activation_and_explicit_trigger_loads_body(
    tmp_path: Path,
) -> None:
    """验证发现阶段不加载正文，而显式名称触发会渐进加载完整工作流。"""
    skill_file = _write_skill(tmp_path, "python-review")
    manager = SkillManager([tmp_path], token_counter=lambda text: len(text.split()))

    available = manager.discover()

    assert set(available) == {"python-review"}
    assert manager.state.available == {"python-review"}
    assert manager.state.loaded_resources == []

    messages = manager.build_messages("Please use $python-review for this patch")

    assert "Follow the review checklist." in messages[0]["content"]
    assert manager.state.activated == {"python-review"}
    assert manager.state.activation_reasons["python-review"] == "explicit:$python-review"
    assert str(skill_file.resolve()) in manager.state.loaded_resources
    assert manager.state.token_cost > 0


def test_skill_implicit_trigger_pinned_and_disabled_policy(tmp_path: Path) -> None:
    """验证关键词与固定启用可激活 Skill，同时配置禁用优先拒绝触发。"""
    _write_skill(tmp_path, "review", triggers="[review python]")
    _write_skill(tmp_path, "release", triggers="[]", body="Prepare release evidence.")
    _write_skill(tmp_path, "unsafe", triggers="[]", body="Do unsafe work.")
    manager = SkillManager(
        [tmp_path],
        pinned={"release"},
        disabled={"unsafe"},
        token_counter=lambda text: len(text.split()),
    )

    messages = manager.build_messages("Please review python changes with $unsafe")

    content = "\n".join(message["content"] for message in messages)
    assert manager.state.discovered == {"release", "review", "unsafe"}
    assert manager.state.activated == {"release", "review"}
    assert manager.state.pinned == {"release"}
    assert manager.state.rejected["unsafe"] == "disabled_by_config"
    assert "Prepare release evidence." in content
    assert "Do unsafe work." not in content


def test_skill_resources_stay_inside_package_and_obey_budget(tmp_path: Path) -> None:
    """验证资源路径不能逃逸 Skill 目录，且总预算耗尽后记录明确拒绝原因。"""
    skill_file = _write_skill(
        tmp_path,
        "docs",
        resources="[references/one.md, references/two.md, ../secret.txt]",
        body="Use the declared references.",
    )
    references = skill_file.parent / "references"
    references.mkdir()
    (references / "one.md").write_text("one two three four", encoding="utf-8")
    (references / "two.md").write_text("five six seven eight", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("must not load", encoding="utf-8")
    manager = SkillManager(
        [tmp_path],
        max_resource_tokens=4,
        token_counter=lambda text: len(text.split()),
    )

    content = manager.build_messages("$docs")[0]["content"]

    assert "one two three four" in content
    assert "five six" not in content
    assert "must not load" not in content
    assert any("outside_skill_directory" in item for item in manager.state.rejected_resources)
    assert any("resource_budget_exhausted" in item for item in manager.state.rejected_resources)


def test_context_preparation_injects_activated_skill_and_reports_cost(tmp_path: Path) -> None:
    """验证 Skill 指令进入当前模型上下文和预算来源，但不会写入持久历史。"""
    _write_skill(tmp_path, "review")
    manager = SkillManager([tmp_path], token_counter=lambda text: len(text.split()))
    service = ContextPreparationService(
        store=_Store(),
        registry=_Registry(),
        system_prompt="base",
        prompt_manager=None,
        token_counter=lambda text: len(text.split()),
        memory_injection_limit=0,
        max_output_tokens=100,
        supports_prompt_cache=False,
        skill_manager=manager,
    )

    prepared = service.prepare("Use $review now")

    assert prepared.skill_state is manager.state
    assert prepared.message_store.model_context_messages[-2]["role"] == "system"
    assert "Follow the review checklist." in prepared.message_store.model_context_messages[-2]["content"]
    assert prepared.compression["budget_breakdown"]["skills"] > 0
    assert prepared.compression["context_sources"][-1]["source"] == "skill"
    assert prepared.message_store.pending_messages == [
        {"role": "user", "content": "Use $review now"}
    ]


def test_turn_state_persists_full_skill_discovery_record() -> None:
    """验证检查点恢复后仍能解释 Skill 的激活原因、资源和成本。"""
    turn = TurnState(
        user_input="$review",
        discovered_skills={"review"},
        skill_discovery_state={
            "available": ["review"],
            "activated": ["review"],
            "activation_reasons": {"review": "explicit:$review"},
            "loaded_resources": ["/skills/review/SKILL.md"],
            "token_cost": 42,
        },
    )

    restored = TurnState.from_record(turn.to_record())

    assert restored.discovered_skills == {"review"}
    assert restored.skill_discovery_state == turn.skill_discovery_state


def test_skills_command_lists_metadata_without_loading_body(tmp_path: Path) -> None:
    """验证 /skills 只刷新轻量目录并展示状态，不因查看命令加载正文。"""
    _write_skill(tmp_path, "review")
    manager = SkillManager([tmp_path])
    console = _Console()

    assert handle_slash(
        "/skills",
        _Registry(),
        object(),
        object(),
        console,
        {"skills": manager},
    )

    rendered = "\n".join(console.lines)
    assert "review (available): Review Python changes" in rendered
    assert "Loaded resources: 0" in rendered
    assert manager.state.loaded_resources == []
