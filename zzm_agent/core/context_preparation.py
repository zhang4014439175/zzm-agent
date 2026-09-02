from __future__ import annotations

import json
from dataclasses import dataclass
from inspect import signature
from typing import Any, Callable

from zzm_agent.core.runtime_messages import ConversationMessageStore
from zzm_agent.core.tool_exposure import ToolExposureState
from zzm_agent.skills import SkillDiscoveryState


@dataclass
class PreparedContext:
    """一次 Segment 的模型上下文、工具目录、消息分层和压缩说明。"""

    tools: list[dict[str, Any]]
    message_store: ConversationMessageStore
    compression: dict[str, Any]
    max_context_tokens: int
    tool_schema_tokens: int
    output_reserve_tokens: int
    skill_state: SkillDiscoveryState | None = None
    tool_exposure_state: ToolExposureState | None = None


class ContextPreparationService:
    """组装并核算单个 Segment 的模型上下文，不负责调用模型或提交状态。"""

    def __init__(
        self,
        *,
        store: Any,
        registry: Any,
        system_prompt: str,
        prompt_manager: Any | None,
        token_counter: Callable[[str], int],
        memory_injection_limit: int,
        max_output_tokens: int | None,
        supports_prompt_cache: bool,
        skill_manager: Any | None = None,
        tool_exposure_manager: Any | None = None,
    ) -> None:
        """保存上下文来源和预算依赖；可选管理器缺失时保持旧上下文行为。

        ``tool_exposure_manager`` 只决定哪些 Schema 发给模型，不替代 Registry 的
        参数校验和权限入口；不传入时继续使用注册表的完整 Schema 列表。
        """
        self.store = store
        self.registry = registry
        self.system_prompt = system_prompt
        self.prompt_manager = prompt_manager
        self.token_counter = token_counter
        self.memory_injection_limit = memory_injection_limit
        self.max_output_tokens = max_output_tokens
        self.supports_prompt_cache = supports_prompt_cache
        self.skill_manager = skill_manager
        self.tool_exposure_manager = tool_exposure_manager

    def build_system_prompt(self, user_input: str) -> str:
        """按原优先级构建系统提示；没有 PromptManager 时返回静态提示。"""
        if self.prompt_manager is None:
            return self.system_prompt
        return self.prompt_manager.build(
            user_input=user_input,
            history=self.store.load_history(),
        )

    def prepare(
        self,
        user_input: str,
        runtime_instructions: list[str] | None = None,
    ) -> PreparedContext:
        """构建工具 Schema、预算参数和分层消息，并返回压缩事实。

        运行时指令只进入当前 MessageStore，不写入持久历史。旧 MemoryStore 若不
        接受新增预算参数，会根据函数签名自动省略，保证迁移期兼容。
        """
        system_prompt = self.build_system_prompt(user_input)
        skill_messages = (
            self.skill_manager.build_messages(user_input)
            if self.skill_manager is not None
            else []
        )
        skill_state = self.skill_manager.state if self.skill_manager is not None else None
        skill_tokens = sum(
            self.token_counter(str(message.get("content") or ""))
            for message in skill_messages
        )
        tool_exposure_state = None
        if self.tool_exposure_manager is not None:
            allowed_tools = (
                self.skill_manager.active_allowed_tools()
                if self.skill_manager is not None
                else ()
            )
            tool_exposure_state = self.tool_exposure_manager.prepare_for_turn(
                user_input,
                allowed_tools=allowed_tools,
            )
            tools = self.tool_exposure_manager.get_schemas()
        else:
            tools = self.registry.get_schemas()
        tool_schema_tokens = self.token_counter(
            json.dumps(tools, ensure_ascii=False, sort_keys=True, default=str)
        ) if tools else 0
        max_context_tokens = max(
            1,
            int(getattr(self.store, "max_context_tokens", 32000) or 32000),
        )
        output_reserve_tokens = max(
            0,
            int(
                self.max_output_tokens
                if self.max_output_tokens is not None
                else min(4096, max_context_tokens // 8)
            ),
        )
        runtime_instruction_tokens = sum(
            self.token_counter(item.strip())
            for item in runtime_instructions or []
            if item.strip()
        )
        kwargs: dict[str, Any] = {
            "system_prompt": system_prompt,
            "user_input": user_input,
            "memory_limit": self.memory_injection_limit,
        }
        optional = {
            "tool_schema_tokens": tool_schema_tokens,
            "output_reserve_tokens": output_reserve_tokens,
            # Skill 会在存储层组装后插入，因此先从历史预算扣除其成本。
            "runtime_instruction_tokens": runtime_instruction_tokens + skill_tokens,
            "prompt_cache_strategy": (
                "provider_native" if self.supports_prompt_cache else "stable_prefix"
            ),
        }
        parameters = signature(self.store.build_turn_messages).parameters
        kwargs.update({key: value for key, value in optional.items() if key in parameters})
        messages, compression = self.store.build_turn_messages(**kwargs)
        if skill_messages:
            messages[max(0, len(messages) - 1):max(0, len(messages) - 1)] = skill_messages
            compression.setdefault("budget_breakdown", {})["skills"] = skill_tokens
            compression.setdefault("context_sources", []).extend(
                {
                    "source": "skill",
                    "name": name,
                    "reason": skill_state.activation_reasons.get(name, ""),
                }
                for name in sorted(skill_state.activated)
            )
            compression["skill_discovery_state"] = skill_state.to_record()
        if tool_exposure_state is not None:
            compression["tool_exposure_state"] = tool_exposure_state.to_record()
            compression.setdefault("budget_breakdown", {})["tool_schemas"] = (
                tool_exposure_state.exposed_schema_tokens
            )
            compression.setdefault("context_sources", []).extend(
                {
                    "source": "tool_schema",
                    "name": name,
                    "reason": tool_exposure_state.activation_reasons.get(
                        name, "always_exposed"
                    ),
                }
                for name in sorted(tool_exposure_state.exposed)
            )
        message_store = ConversationMessageStore.begin_turn(
            persisted_messages=self.store.load_history(),
            model_context_messages=messages,
            user_message={"role": "user", "content": user_input},
        )
        for instruction in runtime_instructions or []:
            if instruction.strip():
                message_store.append_runtime_only(
                    {"role": "system", "content": instruction.strip()}
                )
        return PreparedContext(
            tools=tools,
            message_store=message_store,
            compression=compression,
            max_context_tokens=max_context_tokens,
            tool_schema_tokens=tool_schema_tokens,
            output_reserve_tokens=output_reserve_tokens,
            skill_state=skill_state,
            tool_exposure_state=tool_exposure_state,
        )

    def current_tool_schemas(self) -> tuple[list[dict[str, Any]], int]:
        """返回循环下一次模型调用应使用的最新 Schema 与估算成本。

        模型执行 ``tool_search`` 后，暴露集合会在同一 Segment 内变化；AgentLoop
        每轮通过此入口刷新请求，确保新工具从下一次调用起可见。未配置暴露管理器
        时仍返回完整注册表目录。
        """
        tools = (
            self.tool_exposure_manager.get_schemas()
            if self.tool_exposure_manager is not None
            else self.registry.get_schemas()
        )
        tokens = self.token_counter(
            json.dumps(tools, ensure_ascii=False, sort_keys=True, default=str)
        ) if tools else 0
        return tools, tokens
