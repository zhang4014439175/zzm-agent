from __future__ import annotations

import json
import re
from typing import Any

from zzm_agent.core.tool_results import (
    DisplayMode,
    PlainTextToolRenderer,
    RenderedToolView,
    RendererRegistry,
    ToolProgressEvent,
    ToolRenderContext,
    ToolResult,
)


def _argument(context: ToolRenderContext, name: str, default: str = "") -> str:
    """读取用于展示的工具参数，不执行路径解析或其他可能产生副作用的操作。

    Renderer 只应消费已经发生的事实。该辅助方法把缺失值和非字符串值安全地
    转成短文本；参数不存在时返回默认值，不抛出异常，也不修改上下文。
    """
    value = context.arguments_summary.get(name, default)
    if value is None:
        return default
    return str(value)


def _single_line(text: str, limit: int = 100) -> str:
    """把任意参数压缩成适合终端活动行的单行摘要。

    输入可以包含换行或很长的命令。函数折叠空白并按字符上限截断，返回稳定的
    展示文本；空输入保持为空，不改变原始参数。
    """
    compact = " ".join(str(text).split())
    if len(compact) <= limit:
        return compact
    return compact[: max(0, limit - 3)].rstrip() + "..."


def _result_preview(result: ToolResult) -> str:
    """取得 ToolResult 已准备好的展示预览，避免 Renderer 解析模型自然语言。

    该函数只读取 display_content 中的结构化预览。若结果被截断或已保存为
    Artifact，会附加可观察提示；缺失字段按空结果处理，不影响工具执行状态。
    """
    text = str(result.display_content.get("text") or "")
    notes: list[str] = []
    if result.display_content.get("truncated"):
        notes.append(
            "已折叠 "
            f"{result.display_content.get('hidden_lines', 0)} 行 / "
            f"{result.display_content.get('hidden_chars', 0)} 字符"
        )
    if result.artifacts:
        artifact_ids = [
            str(item.get("artifact_id") or item.get("path") or "<artifact>")
            for item in result.artifacts
        ]
        notes.append(f"完整结果: {', '.join(artifact_ids)}")
    if notes:
        text = f"{text}\n[{'；'.join(notes)}]".strip()
    return text


class LocalToolRenderer(PlainTextToolRenderer):
    """本地工具 Renderer 的公共基类，统一成功、失败和进度展示边界。"""

    action = "执行工具"

    def render_use(self, context: ToolRenderContext) -> RenderedToolView:
        """返回工具开始前的动态活动描述，不执行工具或读取工作区。

        子类通过 activity_detail 提供路径、搜索词或命令摘要。返回值只用于 UI；
        参数缺失时仍给出工具名，保证未知或不完整流事件可以安全降级。
        """
        if context.arguments_summary:
            detail = self.activity_detail(context)
            text = f"{self.action} {detail}".strip()
        else:
            text = context.tool_name
        return RenderedToolView(text=text, metadata={"phase": "running"})

    def activity_detail(self, context: ToolRenderContext) -> str:
        """生成工具特有的活动对象摘要；默认使用工具名作为稳定降级文本。"""
        return context.tool_name

    def render_progress(
        self,
        context: ToolRenderContext,
        event: ToolProgressEvent,
    ) -> RenderedToolView:
        """展示已结构化的进度事实，忽略不存在的可选字段。

        百分比、消息、stdout 和 stderr 按顺序组合。该方法不从文本推断状态，
        不修改进度序号；空进度仍返回工具名，便于终端保持可观察性。
        """
        parts = [self.activity_detail(context)]
        if event.percent is not None:
            parts.append(f"{event.percent:g}%")
        parts.extend(
            _single_line(value, 120)
            for value in (event.message, event.stdout_chunk, event.stderr_chunk)
            if value
        )
        return RenderedToolView(
            text=" · ".join(part for part in parts if part),
            metadata={"phase": "progress", "sequence": event.sequence},
        )

    def render_result(
        self,
        context: ToolRenderContext,
        result: ToolResult,
    ) -> RenderedToolView:
        """展示成功结果的结构化预览、折叠信息和 Artifact 引用。

        返回模式沿用 ToolResult 的策略。结果正文为空时显示“无输出”，避免终端
        看起来像漏掉事件；本方法不修改模型回填内容或 Artifact。
        """
        preview = _result_preview(result) or "无输出"
        return RenderedToolView(
            text=preview,
            display_mode=result.display_mode,
            metadata={"phase": "completed", "status": result.status},
        )

    def render_error(
        self,
        context: ToolRenderContext,
        result: ToolResult,
    ) -> RenderedToolView:
        """展示失败结果，但不根据自然语言重新判断错误类型。

        错误状态来自 ToolResult.status；正文仅使用已经准备好的展示预览。即使
        错误正文为空，也会给出明确失败提示，且不会吞掉 Artifact 引用。
        """
        preview = _result_preview(result) or "工具未返回错误详情"
        return RenderedToolView(
            text=preview,
            display_mode=result.display_mode,
            metadata={"phase": "failed", "status": result.status},
        )


class FileReadRenderer(LocalToolRenderer):
    """为文件读取和目录查看提供路径、行范围与结果预览。"""

    action = "读取"

    def activity_detail(self, context: ToolRenderContext) -> str:
        """用路径和可选行范围说明正在读取什么，缺失参数时回退为当前目录。"""
        path = _argument(context, "path", ".")
        start = _argument(context, "start_line")
        end = _argument(context, "end_line")
        if start or end:
            return f"{path}（行 {start or '起始'}-{end or '末尾'}）"
        return path


class FileEditRenderer(LocalToolRenderer):
    """为精确编辑、覆盖写入和追加写入展示目标文件及变更摘要。"""

    action = "修改"

    def activity_detail(self, context: ToolRenderContext) -> str:
        """展示目标路径和变更规模，不泄露完整写入内容到活动行。"""
        path = _argument(context, "path", "<unknown>")
        replacement = _argument(context, "replacement") or _argument(context, "content")
        if replacement:
            return f"{path}（{len(replacement)} 字符）"
        return path


class SearchRenderer(LocalToolRenderer):
    """为内容搜索和文件名查找展示查询条件与搜索范围。"""

    action = "搜索"

    def activity_detail(self, context: ToolRenderContext) -> str:
        """组合搜索词、文件名模式和路径，参数不完整时仍提供可读摘要。"""
        query = _argument(context, "pattern") or _argument(context, "name_pattern")
        path = _argument(context, "path", ".")
        include = _argument(context, "include")
        detail = f"{query!r} 于 {path}" if query else path
        if include:
            detail += f"（{include}）"
        return detail


class ShellRenderer(LocalToolRenderer):
    """为 Shell 命令展示短命令、工作目录、退出码和输出预览。"""

    action = "运行"
    _EXIT_CODE_RE = re.compile(r"\[exit code:\s*(-?\d+)\]")

    def activity_detail(self, context: ToolRenderContext) -> str:
        """展示压缩后的命令和可选工作目录，避免长命令占满终端。"""
        command = _single_line(_argument(context, "command", context.tool_name), 120)
        cwd = _argument(context, "cwd")
        return f"{command}（cwd: {cwd}）" if cwd else command

    def render_result(
        self,
        context: ToolRenderContext,
        result: ToolResult,
    ) -> RenderedToolView:
        """展示 Shell 输出，并从稳定退出码标记补充状态元数据。

        退出码只用于 UI 元数据，工具成功或失败仍以 ToolResult.status 为准。
        若旧工具结果没有退出码标记则返回 None，保持兼容。
        """
        view = super().render_result(context, result)
        match = self._EXIT_CODE_RE.search(result.model_content)
        metadata = dict(view.metadata)
        metadata["exit_code"] = int(match.group(1)) if match else None
        return RenderedToolView(view.text, view.display_mode, metadata)


def build_local_tool_renderer_registry() -> RendererRegistry:
    """建立内置本地工具到专用 Renderer 的默认映射。

    返回的新 Registry 可由 CLI 或未来 UI 独立持有。未登记的工具继续使用
    PlainTextToolRenderer，确保插件、MCP 和旧测试工具不会因本地集合而失去展示。
    """
    registry = RendererRegistry()
    file_read = FileReadRenderer()
    file_edit = FileEditRenderer()
    search = SearchRenderer()
    shell = ShellRenderer()

    for name in ("read_file", "list_directory", "file_info"):
        registry.register_tool(name, file_read)
    for name in ("file_edit", "write_file", "file_append"):
        registry.register_tool(name, file_edit)
    for name in ("grep_search", "find_files", "rg", "search"):
        registry.register_tool(name, search)
    registry.register_tool("run_shell", shell)
    return registry


def parse_tool_arguments(raw: str) -> dict[str, Any]:
    """把已收集的工具参数 JSON 转成 Renderer 可用字典。

    流式阶段可能只收到半段 JSON，此时返回空字典并等待后续事件；完整但非对象
    的 JSON 也安全降级为空字典。函数不校验工具 schema，权限和执行逻辑不受影响。
    """
    if not raw.strip():
        return {}
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}
