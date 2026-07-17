from __future__ import annotations

import json
from dataclasses import dataclass
from inspect import signature
from typing import Any, Callable

from zzm_agent.core.runtime_messages import ConversationMessageStore


@dataclass
class PreparedContext:
    """一次 Segment 的模型上下文、工具目录、消息分层和压缩说明。"""

    tools: list[dict[str, Any]]
    message_store: ConversationMessageStore
    compression: dict[str, Any]
    max_context_tokens: int
    tool_schema_tokens: int
    output_reserve_tokens: int


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
    ) -> None:
        """保存上下文来源和预算依赖；所有对象仍由原运行时持有并负责生命周期。"""
        self.store = store
        self.registry = registry
        self.system_prompt = system_prompt
        self.prompt_manager = prompt_manager
        self.token_counter = token_counter
        self.memory_injection_limit = memory_injection_limit
        self.max_output_tokens = max_output_tokens
        self.supports_prompt_cache = supports_prompt_cache

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
            "runtime_instruction_tokens": runtime_instruction_tokens,
            "prompt_cache_strategy": (
                "provider_native" if self.supports_prompt_cache else "stable_prefix"
            ),
        }
        parameters = signature(self.store.build_turn_messages).parameters
        kwargs.update({key: value for key, value in optional.items() if key in parameters})
        messages, compression = self.store.build_turn_messages(**kwargs)
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
        )
