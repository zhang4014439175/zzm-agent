from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from zzm_agent.core.agent_loop import AgentLoop
from zzm_agent.core.language_policy import (
    ResponseLanguageDecision,
    resolve_response_language,
)
from zzm_agent.core.model_stream import ModelStreamEvent, ModelStreamEventKind
from zzm_agent.core.runtime_state import (
    ApplicationState,
    ConversationState,
    TurnState,
    TurnStatus,
)
from zzm_agent.core.segments import SegmentResult
from zzm_agent.core.state_serialization import StateSnapshotStore
from zzm_agent.memory.pinned_context import PinnedContext


StreamEventCallback = Callable[[ModelStreamEvent], None]


@dataclass(frozen=True)
class QueryResult:
    """封装一次用户消息从开始到任务终态的完整结果。

    ``reply`` 是最终交付文本，``events`` 供 CLI/JSONL 等客户端回放过程，
    ``turn`` 保存最终状态与终止原因，``segments`` 则保留长任务经历的所有内部
    执行段。简单任务通常只有一个 completed Segment。
    """

    reply: str
    events: list[ModelStreamEvent] = field(default_factory=list)
    turn: TurnState | None = None
    response_language: ResponseLanguageDecision | None = None
    segments: tuple[SegmentResult, ...] = ()


class QueryEngine:
    """CLI 与未来客户端共享的跨 Segment、跨 Turn 编排边界。"""

    def __init__(
        self,
        *,
        agent_loop: AgentLoop,
        config: dict[str, Any] | None = None,
        application_state: ApplicationState | None = None,
        conversation_state: ConversationState | None = None,
        snapshot_store: StateSnapshotStore[ConversationState] | None = None,
    ) -> None:
        """绑定执行循环、会话状态、配置和可选快照存储。

        初始化时把会话注册到 ApplicationState，并统一 ConversationState 与
        AgentLoop 使用的 ArtifactStore：恢复出的会话已有 Artifact 时优先采用
        恢复记录，否则让会话引用当前循环新建的 Store。该方法不执行模型请求。
        """
        self.agent_loop = agent_loop
        self.config = config or {}
        session_id = str(getattr(agent_loop.store, "session_id", "default"))
        self.application_state = application_state or ApplicationState(
            active_session_id=session_id
        )
        self.conversation_state = conversation_state or ConversationState(
            session_id=session_id
        )
        self.snapshot_store = snapshot_store
        self.application_state.active_session_id = self.conversation_state.session_id
        self.application_state.conversations[self.conversation_state.session_id] = (
            self.conversation_state
        )
        if self.conversation_state.artifacts.records:
            self.agent_loop.artifact_store = self.conversation_state.artifacts
        else:
            self.conversation_state.artifacts = self.agent_loop.artifact_store

    @classmethod
    def with_snapshot_path(
        cls,
        *,
        agent_loop: AgentLoop,
        snapshot_path: str | Path,
        config: dict[str, Any] | None = None,
        application_state: ApplicationState | None = None,
        conversation_state: ConversationState | None = None,
    ) -> "QueryEngine":
        """使用给定路径创建带自动状态快照能力的 QueryEngine。

        这是构造便利方法，只负责包装 ``StateSnapshotStore``，其余状态绑定规则
        与直接调用构造函数完全一致。
        """
        return cls(
            agent_loop=agent_loop,
            config=config,
            application_state=application_state,
            conversation_state=conversation_state,
            snapshot_store=StateSnapshotStore(snapshot_path),
        )

    def submit_message(
        self,
        user_input: str,
        *,
        stream: bool = True,
        on_stream_event: StreamEventCallback | None = None,
        on_text_chunk: Callable[[str], None] | None = None,
        language_input: str | None = None,
    ) -> QueryResult:
        """提交一条用户消息，并持续执行直到得到真正的任务终态。

        方法先确定回复语言并创建 Turn 快照，然后逐段调用 AgentLoop。遇到
        yielded 会从检查点生成内部续段指令，按下一段固定开销压缩历史并自动
        继续；遇到 completed、blocked、failed 或 cancelled 才停止。空的完成
        回复会被完成门禁改为 blocked，连续让出超过配置上限也会明确阻塞，避免
        静默停止或无限消耗。最终返回全部事件、分段记录和终态。
        """
        events: list[ModelStreamEvent] = []

        def emit(event: ModelStreamEvent) -> None:
            """同时记录规范化事件并转发给当前客户端回调。"""
            events.append(event)
            if on_stream_event is not None:
                on_stream_event(event)

        # 1. 回复语言属于整个用户任务，自动续段时沿用同一决策。
        language_decision = resolve_response_language(
            language_input if language_input is not None else user_input,
            previous_language=self.conversation_state.response_language,
            config=self.config,
        )
        self.conversation_state.response_language = language_decision.language
        self.conversation_state.response_language_source = language_decision.source

        self.conversation_state.active_turn = TurnState(user_input=user_input)
        self.conversation_state.active_turn.start()
        emit(
            ModelStreamEvent.status(
                "turn.started",
                response_language=language_decision.language,
                language_source=language_decision.source,
            )
        )
        self._save_snapshot("turn.started")

        # 2. 执行一个或多个 Segment；yielded 只是内部检查点，不向用户结束任务。
        segments: list[SegmentResult] = []
        continuation_count = 0
        max_auto_continuations = max(
            1,
            int(self.config.get("agent", {}).get("max_auto_continuations", 8)),
        )
        segment_input = user_input
        reply = ""
        while True:
            try:
                segment = self.agent_loop.run_segment(
                    segment_input,
                    stream=stream,
                    on_text_chunk=on_text_chunk,
                    on_stream_event=emit,
                    runtime_instructions=[language_decision.instruction],
                )
            except Exception as exc:
                last_turn = self.agent_loop.last_turn_state
                if last_turn is not None:
                    self.conversation_state.active_turn = last_turn
                elif self.conversation_state.active_turn is not None:
                    self.conversation_state.active_turn.fail(str(exc))
                emit(ModelStreamEvent.error(str(exc)))
                emit(
                    ModelStreamEvent.termination(
                        "failed",
                        str(exc),
                    )
                )
                self._save_snapshot("turn.failed")
                raise

            segments.append(segment)
            if segment.turn is not None:
                self.conversation_state.active_turn = segment.turn
            if segment.status is not TurnStatus.YIELDED:
                if (
                    segment.status is TurnStatus.COMPLETED
                    and not segment.reply.strip()
                ):
                    reply = (
                        "任务已阻塞：执行段报告完成，但没有提供有效最终答复。"
                        "这属于完成协议不完整，请继续任务或明确说明阻塞原因。"
                    )
                    blocked_turn = TurnState(user_input=user_input)
                    blocked_turn.start_loop()
                    blocked_turn.block(
                        reply,
                        reason="completion_gate_empty_reply",
                    )
                    self.conversation_state.active_turn = blocked_turn
                    segments.append(
                        SegmentResult(
                            status=TurnStatus.BLOCKED,
                            reason="completion_gate_empty_reply",
                            reply=reply,
                            turn=blocked_turn,
                        )
                    )
                else:
                    reply = segment.reply
                break

            continuation_count += 1
            # 3. 根据检查点构造续段输入，并显式带上可追溯 Artifact 来源。
            checkpoint = segment.checkpoint
            artifact_ids = [
                str(item.get("artifact_id"))
                for item in checkpoint.get("artifacts", [])
                if isinstance(item, dict) and item.get("artifact_id")
            ]
            artifact_note = (
                f" Artifact sources: {', '.join(artifact_ids)}."
                if artifact_ids
                else ""
            )
            segment_input = (
                "[CONTINUE_TASK_FROM_CHECKPOINT]\n"
                f"Original user task: {user_input}\n"
                f"Checkpoint reason: {segment.reason}.\n"
                f"Remaining work: {segment.remaining_work_summary or 'Reassess and continue.'}"
                f"{artifact_note}\n"
                "Continue this task. A segment boundary is not completion. "
                "Use the source-linked history, then return a final answer or a "
                "concrete blocker."
            )
            context_window = self.agent_loop.last_context_window
            breakdown = context_window.get("budget_breakdown", {}) or {}
            pinned_message = PinnedContext.from_turn(
                user_input=segment_input,
                history=self.agent_loop.store.load_history(),
            ).to_message()
            continuation_tokens = self.agent_loop.store.estimate_messages_tokens(
                [{"role": "user", "content": segment_input}]
                + ([pinned_message] if pinned_message is not None else [])
            )
            fixed_context_tokens = sum(
                int(breakdown.get(name, 0) or 0)
                for name in (
                    "system_prompt",
                    "instruction_files",
                    "memory",
                    "tool_schema",
                    "reflection_prompt",
                    "output_reserve",
                )
            )
            compact_budget = max(
                0,
                int(
                    context_window.get(
                        "max_context_tokens",
                        getattr(self.agent_loop.store, "max_context_tokens", 0),
                    )
                    or 0
                )
                - fixed_context_tokens
                - continuation_tokens,
            )
            compact_preview = self.agent_loop.store.compress_history(
                budget_tokens=compact_budget
            )
            # 4. yielded 事件用于观察内部换段；只有保险丝耗尽才转为用户可见阻塞。
            emit(
                ModelStreamEvent.status(
                    "segment.yielded",
                    reason=segment.reason,
                    segment=continuation_count,
                    tool_iterations=segment.tool_iterations,
                    tool_calls=segment.tool_calls,
                    compression_applied=compact_preview.get("applied", False),
                    compression_strategy=compact_preview.get(
                        "compression_strategy", "none"
                    ),
                    context_sources=self.agent_loop.last_context_window.get(
                        "context_sources", []
                    ),
                )
            )
            self._save_snapshot("turn.yielded")
            if continuation_count >= max_auto_continuations:
                reply = (
                    "任务已阻塞：已连续自动续段 "
                    f"{continuation_count} 次，仍未得到完成或明确阻塞结果。"
                    "为避免无界资源消耗，请检查任务范围或提高 "
                    "agent.max_auto_continuations 后继续。"
                )
                blocked_turn = TurnState(user_input=user_input)
                blocked_turn.start_loop()
                blocked_turn.block(reply, reason="auto_continuation_limit")
                self.conversation_state.active_turn = blocked_turn
                segments.append(
                    SegmentResult(
                        status=TurnStatus.BLOCKED,
                        reason="auto_continuation_limit",
                        reply=reply,
                        turn=blocked_turn,
                    )
                )
                break

        # 5. 汇总跨 Segment 状态，并且只发送一次最终消息与终止事件。
        self.conversation_state.usage = self.agent_loop.cumulative_usage
        self.conversation_state.usage_state = self.agent_loop.usage_state
        self.conversation_state.permissions = self.agent_loop.permission_state
        turn = self.conversation_state.active_turn
        status = str(getattr(turn.status, "value", turn.status)) if turn else "failed"
        termination = turn.termination if turn is not None else None
        reason = termination.reason if termination is not None else "missing_turn_state"
        provider_finish_reason = (
            termination.provider_finish_reason
            if termination is not None
            else None
        )
        recovery_attempts = (
            termination.recovery_attempts if termination is not None else 0
        )
        if not any(
            event.kind is ModelStreamEventKind.FINAL_MESSAGE
            and event.text == reply
            for event in events
        ):
            emit(
                ModelStreamEvent.final_message(
                    reply,
                    status=status,
                    reason=reason,
                    provider_finish_reason=provider_finish_reason,
                )
            )
        emit(
            ModelStreamEvent.termination(
                status,
                reason,
                provider_finish_reason=provider_finish_reason,
                recovery_attempts=recovery_attempts,
            )
        )
        self._save_snapshot(f"turn.{status}")
        return QueryResult(
            reply=reply,
            events=events,
            turn=self.conversation_state.active_turn,
            response_language=language_decision,
            segments=tuple(segments),
        )

    def _save_snapshot(self, reason: str) -> None:
        """在配置快照存储时持久化当前会话及本次状态变化原因。

        未配置 snapshot_store 时直接返回；保存内容以 ConversationState 为事实源，
        ``reason`` 仅作为检索和诊断元数据，不参与状态机判断。
        """
        if self.snapshot_store is None:
            return
        self.snapshot_store.save(
            self.conversation_state,
            state_type="conversation",
            metadata={
                "reason": reason,
                "session_id": self.conversation_state.session_id,
            },
        )
