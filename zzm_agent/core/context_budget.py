from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ContextBudgetEntry:
    """描述单个上下文来源占用的 Token 预算。

    ``source`` 是稳定的机器可读分类名，``tokens`` 是当前请求的估算占用，
    ``detail`` 用于向使用者解释该分类包含了什么。``preserved`` 表示压缩历史时
    是否应优先保留该来源，便于后续压缩策略在不理解业务内容的情况下做决策。
    """

    source: str
    tokens: int
    detail: str = ""
    preserved: bool = True

    def to_record(self) -> dict[str, Any]:
        """转换为可序列化记录，并把负数 Token 防御性归零。

        返回值只包含 JSON 兼容字段，可直接写入状态快照、日志或 CLI 状态输出。
        该方法不修改当前不可变对象。
        """
        return {
            "source": self.source,
            "tokens": max(0, int(self.tokens)),
            "detail": self.detail,
            "preserved": self.preserved,
        }


@dataclass(frozen=True)
class ContextBudget:
    """汇总一次模型请求的上下文窗口分配情况。

    对象同时保存各来源预算、压缩信息、Prompt Cache 策略和可追溯来源，
    让 AgentLoop、QueryEngine 与 CLI 使用同一份上下文事实，而不是分别计算。
    """

    max_context_tokens: int
    entries: tuple[ContextBudgetEntry, ...] = ()
    compression_applied: bool = False
    compression_strategy: str = "none"
    prompt_cache_strategy: str = "stable_prefix"
    sources: tuple[dict[str, Any], ...] = field(default_factory=tuple)

    @property
    def total_tokens(self) -> int:
        """返回所有预算分类的总占用，异常负值按零处理。"""
        return sum(max(0, int(entry.tokens)) for entry in self.entries)

    @property
    def remaining_tokens(self) -> int:
        """返回上下文窗口的剩余容量；超出窗口时返回零而不是负数。"""
        return max(0, int(self.max_context_tokens) - self.total_tokens)

    @property
    def over_budget(self) -> bool:
        """判断当前总占用是否已经超过模型上下文窗口。"""
        return self.total_tokens > max(0, int(self.max_context_tokens))

    def to_record(self) -> dict[str, Any]:
        """生成完整的 JSON 兼容预算快照。

        快照包含总量、余量、是否超限、各分类明细、压缩方式和上下文来源，
        用于持久化以及向 `/status` 等诊断入口解释本轮上下文如何构成。
        """
        return {
            "max_context_tokens": max(0, int(self.max_context_tokens)),
            "total_tokens": self.total_tokens,
            "remaining_tokens": self.remaining_tokens,
            "over_budget": self.over_budget,
            "entries": [entry.to_record() for entry in self.entries],
            "compression_applied": self.compression_applied,
            "compression_strategy": self.compression_strategy,
            "prompt_cache_strategy": self.prompt_cache_strategy,
            "sources": [dict(source) for source in self.sources],
        }
