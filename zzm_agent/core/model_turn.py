from __future__ import annotations

from collections.abc import Callable
from typing import Any

from zzm_agent.core.model_adapter import OpenAIChatCompletionsAdapter
from zzm_agent.core.model_stream import ModelStreamEvent
from zzm_agent.core.observability import TokenUsage


class ModelTurnDriver:
    """执行一次模型请求并把 Provider 输出归一为 AgentLoop 可消费的 Turn 结果。

    Driver 只负责模型 I/O、流式片段拼接、工具调用增量组装和可见文本事件。
    Prompt 组装、工具策略、恢复决策与状态转换仍由 AgentLoop 持有，并通过明确
    回调注入。这样迁移期间能保留原有 Provider 降级和伪 XML 工具兼容行为。
    """

    def __init__(
        self,
        *,
        adapter: OpenAIChatCompletionsAdapter,
        build_request: Callable[[list[dict[str, Any]], list[dict[str, Any]], bool], dict[str, Any]],
        retry_without_tool_choice: Callable[[Exception, dict[str, Any]], Any],
        provider_rejects_streaming: Callable[[Exception], bool],
        usage_from_sdk: Callable[[Any], TokenUsage],
        extract_text_tool_calls: Callable[[str], list[dict[str, Any]]],
        text_tool_call_start: Callable[[str, list[dict[str, Any]]], int],
        build_tool_call_record: Callable[[str, str, str], dict[str, Any]],
    ) -> None:
        """保存模型适配器和纯计算依赖，不创建会话状态或发起请求。

        所有回调都来自现有 AgentLoop 兼容层：输入是消息、工具或 Provider 对象，
        输出是请求参数、Usage 或工具记录。构造失败会直接暴露，实例本身无副作用。
        """
        self.adapter = adapter
        self.build_request = build_request
        self.retry_without_tool_choice = retry_without_tool_choice
        self.provider_rejects_streaming = provider_rejects_streaming
        self.usage_from_sdk = usage_from_sdk
        self.extract_text_tool_calls = extract_text_tool_calls
        self.text_tool_call_start = text_tool_call_start
        self.build_tool_call_record = build_tool_call_record

    def complete_once(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> tuple[str, list[dict[str, Any]], bool, TokenUsage, str | None]:
        """执行一次非流式模型请求并返回正文、工具调用、中断标志、Usage 和结束原因。

        Provider 拒绝 tool_choice 时使用注入的兼容重试。原生工具调用为空时继续
        支持文本工具协议；识别到文本工具后清空正文，避免伪协议泄露给用户。
        """
        kwargs = self.build_request(messages, tools, False)
        try:
            response = self.adapter.create_completion(kwargs)
        except Exception as exc:
            response = self.retry_without_tool_choice(exc, kwargs)
        normalized = self.adapter.normalize_response(response)
        content = normalized.content
        tool_calls = normalized.tool_calls
        if not tool_calls:
            tool_calls = self.extract_text_tool_calls(content)
            if tool_calls:
                content = ""
        return (
            content,
            tool_calls,
            False,
            self.usage_from_sdk(normalized.raw_usage),
            normalized.finish_reason,
        )

    def stream_once(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        on_text_chunk: Callable[[str], None] | None,
        on_stream_event: Callable[[ModelStreamEvent], None] | None = None,
    ) -> tuple[str, list[dict[str, Any]], bool, TokenUsage, str | None]:
        """消费一次流式模型响应，按调用索引拼接工具片段并发布分层事件。

        输入回调可为空。流中断且已经出现正文时返回 interrupted=True，调用方可
        丢弃不完整工具状态；Provider 不支持流式时降级为非流式请求。该方法不
        修改 Conversation 或 Loop 状态，状态提交由外层编排器决定。
        """
        kwargs = self.build_request(messages, tools, True)
        text_parts: list[str] = []
        emitted_text_length = 0
        usage = TokenUsage()
        finish_reason: str | None = None
        tool_call_map: dict[int, dict[str, Any]] = {}

        try:
            try:
                response = self.adapter.create_completion(kwargs)
            except Exception as exc:
                try:
                    response = self.retry_without_tool_choice(exc, kwargs)
                except Exception as retry_exc:
                    if self.provider_rejects_streaming(retry_exc):
                        return self.complete_once(messages, tools)
                    raise
            for chunk in self.adapter.iter_stream_chunks(response):
                if chunk.finish_reason is not None:
                    finish_reason = str(chunk.finish_reason)
                chunk_usage = self.usage_from_sdk(chunk.raw_usage)
                if chunk_usage.has_tokens():
                    usage.add(chunk_usage)
                    if on_stream_event is not None:
                        on_stream_event(ModelStreamEvent.usage_delta(chunk_usage.to_record()))
                if chunk.reasoning_summary and on_stream_event is not None:
                    on_stream_event(ModelStreamEvent.reasoning_summary(chunk.reasoning_summary))

                if chunk.content_delta:
                    text_parts.append(chunk.content_delta)
                    if on_text_chunk is not None or on_stream_event is not None:
                        full_text = "".join(text_parts)
                        tool_start = self.text_tool_call_start(full_text, tools)
                        visible_end = tool_start if tool_start >= 0 else len(full_text)
                        if emitted_text_length < visible_end:
                            visible = full_text[emitted_text_length:visible_end]
                            if on_text_chunk is not None:
                                on_text_chunk(visible)
                            if on_stream_event is not None and visible:
                                on_stream_event(ModelStreamEvent.content_delta(visible))
                            emitted_text_length = visible_end

                for delta in chunk.tool_call_deltas:
                    record = tool_call_map.setdefault(
                        delta.index,
                        self.build_tool_call_record("", "", ""),
                    )
                    if delta.tool_call_id:
                        record["id"] = delta.tool_call_id
                    if delta.name_delta:
                        record["function"]["name"] += delta.name_delta
                    if delta.arguments_delta:
                        record["function"]["arguments"] += delta.arguments_delta
                    if on_stream_event is not None:
                        on_stream_event(
                            ModelStreamEvent.tool_call_delta(
                                tool_call_id=record.get("id") or None,
                                tool_name=delta.name_delta or None,
                                arguments_delta=delta.arguments_delta,
                                index=delta.index,
                            )
                        )
        except (KeyboardInterrupt, GeneratorExit):
            return "".join(text_parts), [], True, usage, finish_reason
        except Exception:
            if text_parts:
                return "".join(text_parts), [], True, usage, finish_reason
            raise

        tool_calls = [tool_call_map[index] for index in sorted(tool_call_map)]
        content = "".join(text_parts)
        if not tool_calls:
            tool_calls = self.extract_text_tool_calls(content)
            if tool_calls:
                return "", tool_calls, False, usage, finish_reason
        if (on_text_chunk is not None or on_stream_event is not None) and emitted_text_length < len(content):
            visible = content[emitted_text_length:]
            if on_text_chunk is not None:
                on_text_chunk(visible)
            if on_stream_event is not None and visible:
                on_stream_event(ModelStreamEvent.content_delta(visible))
        if on_stream_event is not None:
            on_stream_event(ModelStreamEvent.final_message(content))
        return content, tool_calls, False, usage, finish_reason
