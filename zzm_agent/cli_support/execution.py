from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from zzm_agent.core.model_stream import ModelStreamEvent
from zzm_agent.runtime.events import RuntimeEvent
from zzm_agent.cli_support.repl import format_runtime_exception

def _build_exec_prompt(args: argparse.Namespace, stdin_text: str = "") -> str:
    prompt = " ".join(getattr(args, "prompt", []) or []).strip()
    if getattr(args, "stdin", False):
        stdin_text = stdin_text.strip()
        if stdin_text:
            if prompt:
                return f"{prompt}\n\nInput from stdin:\n{stdin_text}"
            return stdin_text
    return prompt


def _write_exec_output_file(path: str | Path, text: str) -> None:
    output_path = Path(path).expanduser()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(text, encoding="utf-8")


def _json_event_line(event: Any) -> str:
    """把模型兼容事件或统一 RuntimeEvent 编码为单行 JSON 事实。"""
    return json.dumps(
        {"type": "event", **event.to_record()},
        ensure_ascii=False,
        sort_keys=True,
    )


def _json_result_line(reply: str, result: Any) -> str:
    response_language = getattr(result, "response_language", None)
    return json.dumps(
        {
            "type": "result",
            "reply": reply,
            "response_language": getattr(response_language, "language", None),
            "language_source": getattr(response_language, "source", None),
        },
        ensure_ascii=False,
        sort_keys=True,
    )


def run_exec(
    runtime: dict[str, Any],
    args: argparse.Namespace,
    *,
    stdin_text: str = "",
    stdout: Any | None = None,
    stderr: Any | None = None,
) -> int:
    """执行一次供脚本、CI 或管道使用的非交互任务。

    方法组合命令行和 stdin 输入，调用共享 QueryEngine，并按 ``--json`` 决定
    输出统一 RuntimeEvent 事实或纯最终文本。真实 QueryEngine 返回版本化事件时
    优先使用该记录；旧测试替身只提供 ModelStreamEvent 时保留兼容降级。异常写入
    结构化错误或 stderr，返回非零退出码；输出文件只在任务成功后写入。
    """
    import sys

    stdout = stdout or sys.stdout
    stderr = stderr or sys.stderr
    prompt = _build_exec_prompt(args, stdin_text)
    if not prompt:
        stderr.write("zzm-agent exec requires a prompt or --stdin content.\n")
        return 2

    query_engine = runtime.get("query_engine")
    if query_engine is None:
        stderr.write("zzm-agent exec requires QueryEngine in the runtime.\n")
        return 1

    json_output = bool(getattr(args, "json_output", False))
    events: list[ModelStreamEvent] = []

    def on_stream_event(event: ModelStreamEvent) -> None:
        """缓存兼容流事件；真实 QueryEngine 返回后优先输出统一运行事实。"""
        events.append(event)

    try:
        result = query_engine.submit_message(
            prompt,
            stream=json_output,
            on_stream_event=on_stream_event if json_output else None,
            language_input=prompt,
        )
    except Exception as exc:
        if json_output:
            stdout.write(
                json.dumps(
                    {"type": "error", "message": format_runtime_exception(exc, runtime)},
                    ensure_ascii=False,
                    sort_keys=True,
                )
                + "\n"
            )
        else:
            stderr.write(format_runtime_exception(exc, runtime) + "\n")
        return 1

    reply = result.reply
    if json_output:
        factual_events = getattr(result, "runtime_events", None) or events
        for event in factual_events:
            stdout.write(_json_event_line(event) + "\n")
        flush = getattr(stdout, "flush", None)
        if callable(flush):
            flush()
    output_path = getattr(args, "output_path", None)
    if output_path:
        _write_exec_output_file(output_path, reply)
    if json_output:
        stdout.write(_json_result_line(reply, result) + "\n")
    elif not output_path:
        stdout.write(reply)
        if reply and not reply.endswith("\n"):
            stdout.write("\n")
    return 0



