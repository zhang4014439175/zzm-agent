"""最小 MCP stdio 客户端：把一个受信任配置的服务暴露为受管工具。"""

from __future__ import annotations

import json
import queue
import subprocess
import threading
from collections import deque
from dataclasses import dataclass, field
from itertools import count
from typing import Any

from zzm_agent.core.tool_registry import ToolRegistry


class MCPError(RuntimeError):
    """MCP 握手、协议或远程调用失败时提供带上下文的错误。"""


@dataclass
class MCPConnection:
    """一个 stdio MCP 服务的连接状态和由它发现的工具名称。"""

    name: str
    command: list[str]
    timeout_seconds: float = 15.0
    process: subprocess.Popen[str] | None = None
    tools: list[str] = field(default_factory=list)
    server_info: dict[str, Any] = field(default_factory=dict)


class StdioMCPClient:
    """通过换行 JSON-RPC 与一个 MCP stdio 服务握手、发现工具并安全关闭。

    该最小版本只管理单个已配置服务。服务声明的工具会注册进 ToolRegistry，因而
    模型不能绕过既有参数校验、权限网关和结果记录；连接错误被转换为 MCPError，
    不会中断其他本地工具。每个请求都有本地超时，关闭时会先通知服务再终止进程。
    """

    def __init__(self, name: str, command: list[str], *, timeout_seconds: float = 15.0):
        """保存服务标识、启动参数和请求超时，但此时不创建子进程。

        ``name`` 用于隔离工具名称，``command`` 会直接作为参数数组交给子进程，
        ``timeout_seconds`` 同时约束握手和工具请求。空命令立即拒绝；其余启动
        错误延迟到 ``connect()`` 报告。
        """
        if not command:
            raise ValueError("MCP command must not be empty")
        self.connection = MCPConnection(name=name, command=list(command), timeout_seconds=timeout_seconds)
        self._ids = count(1)
        self._request_lock = threading.Lock()
        self._write_lock = threading.Lock()
        self._pending: dict[int, queue.Queue[dict[str, Any] | MCPError]] = {}
        self._reader_thread: threading.Thread | None = None
        self._stderr_thread: threading.Thread | None = None
        self._stderr_tail: deque[str] = deque(maxlen=50)

    def connect(self, registry: ToolRegistry) -> MCPConnection:
        """启动服务、握手并把发现的工具原子注册到指定 Registry。

        成功返回包含服务信息和本地工具名的连接状态。重复调用直接返回现有连接。
        启动、协议或注册失败时会撤销本轮已经加入的工具、关闭子进程并继续抛出
        原异常，使启动层可以隔离该服务而不污染其他工具。
        """
        if self.connection.process is not None:
            return self.connection
        try:
            process = subprocess.Popen(
                self.connection.command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, text=True, encoding="utf-8", bufsize=1,
            )
        except OSError as exc:
            raise MCPError(f"Unable to start MCP server {self.connection.name}: {exc}") from exc
        self.connection.process = process
        self._start_pipe_readers()
        try:
            initialized = self._request("initialize", {
                "protocolVersion": "2025-03-26",
                "capabilities": {}, "clientInfo": {"name": "zzm-agent", "version": "0.1.2"},
            })
            self.connection.server_info = dict(initialized.get("serverInfo") or {})
            self._notify("notifications/initialized")
            response = self._request("tools/list", {})
            for tool in response.get("tools", []):
                self._register_tool(registry, tool)
            return self.connection
        except Exception:
            # 工具发现必须是原子的：中途遇到坏 Schema 或名称冲突时，不能留下
            # 指向已关闭连接的半套工具。
            for name in self.connection.tools:
                registry.tools.pop(name, None)
            self.connection.tools.clear()
            self.close()
            raise

    def _start_pipe_readers(self) -> None:
        """启动唯一 stdout 分发线程并持续排空 stderr，防止日志填满管道。

        stdout 只能由一个线程读取。它按 JSON-RPC id 把响应送给等待中的请求，
        合法通知会被忽略，未知的服务端请求会收到“方法不支持”响应。stderr 只
        保留有限尾部用于错误说明，不会无限占用内存。
        """
        process = self.connection.process
        assert process is not None and process.stdout is not None and process.stderr is not None
        self._reader_thread = threading.Thread(
            target=self._reader_loop,
            name=f"mcp-{self.connection.name}-stdout",
            daemon=True,
        )
        self._stderr_thread = threading.Thread(
            target=self._stderr_loop,
            name=f"mcp-{self.connection.name}-stderr",
            daemon=True,
        )
        self._reader_thread.start()
        self._stderr_thread.start()

    def _reader_loop(self) -> None:
        """持续解析 stdout；坏消息只结束当前连接并唤醒所有等待请求。"""
        process = self.connection.process
        assert process is not None and process.stdout is not None
        failure: MCPError | None = None
        try:
            for line in process.stdout:
                try:
                    message = json.loads(line)
                except json.JSONDecodeError:
                    failure = MCPError(
                        f"MCP server {self.connection.name} sent invalid JSON"
                    )
                    break
                request_id = message.get("id")
                if request_id is not None and "method" not in message:
                    waiter = self._pending.get(request_id)
                    if waiter is not None:
                        waiter.put(message)
                    continue
                if request_id is not None and "method" in message:
                    self._write({
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "error": {"code": -32601, "message": "Client method not supported"},
                    })
                # 无 id 的通知允许与响应交错；最小版本暂不向上层发布。
        except (OSError, ValueError) as exc:
            failure = MCPError(f"MCP server {self.connection.name} stdout failed: {exc}")
        finally:
            failure = failure or MCPError(
                f"MCP server {self.connection.name} closed stdout"
            )
            for waiter in list(self._pending.values()):
                waiter.put(failure)

    def _stderr_loop(self) -> None:
        """持续排空服务日志，只保存最近 50 行供诊断。"""
        process = self.connection.process
        assert process is not None and process.stderr is not None
        try:
            for line in process.stderr:
                self._stderr_tail.append(line.rstrip())
        except (OSError, ValueError):
            return

    def _register_tool(self, registry: ToolRegistry, tool: dict[str, Any]) -> None:
        """将一个远端 Schema 转成受管工具并记录本地名称。

        工具名增加 ``mcp.<服务名>.`` 前缀，输入 Schema 原样交给统一参数校验，
        风险固定为 medium 以复用权限确认。缺失名称或本地名称冲突会失败，外层
        ``connect()`` 负责撤销此前已经注册的同批工具。
        """
        remote_name = tool.get("name")
        if not isinstance(remote_name, str) or not remote_name:
            raise MCPError("MCP tools/list returned a tool without a name")
        local_name = f"mcp.{self.connection.name}.{remote_name}"
        registry.register_external_tool(
            name=local_name, description=str(tool.get("description") or remote_name),
            parameters=dict(tool.get("inputSchema") or {}),
            handler=lambda **arguments: self.call_tool(remote_name, arguments),
            risk_level="medium", group="MCP", timeout_seconds=self.connection.timeout_seconds,
            source="mcp", server_name=self.connection.name, lazy_schema=True,
        )
        self.connection.tools.append(local_name)

    def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        """向服务发送工具名和已校验参数，返回 MCP content 或原始结果对象。

        JSON-RPC error 和 MCP ``isError`` 都转换为 ``MCPError``，让现有工具恢复
        策略统一处理；该方法不修改 Registry，只消费当前连接。
        """
        result = self._request("tools/call", {"name": name, "arguments": arguments})
        if result.get("isError"):
            raise MCPError(f"MCP tool {name} failed: {result.get('content', result)}")
        return result.get("content", result)

    def _notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        """发送无需响应的 JSON-RPC 通知；连接不可用或写入失败时明确报错。"""
        self._write({"jsonrpc": "2.0", "method": method, **({"params": params} if params else {})})

    def _request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        """发送一个有 id 的请求并等待唯一读线程分发对应响应。

        请求按顺序执行，等待时间受连接超时约束；超时会附带最近一行 stderr。
        无论成功失败都会删除等待槽，迟到响应因此会被安全忽略。远端 error、
        stdout 关闭和非对象 result 均转换为 ``MCPError``。
        """
        request_id = next(self._ids)
        waiter: queue.Queue[dict[str, Any] | MCPError] = queue.Queue(maxsize=1)
        with self._request_lock:
            self._pending[request_id] = waiter
            try:
                self._write({
                    "jsonrpc": "2.0", "id": request_id,
                    "method": method, "params": params,
                })
                try:
                    response = waiter.get(timeout=self.connection.timeout_seconds)
                except queue.Empty as exc:
                    detail = f"; stderr: {self._stderr_tail[-1]}" if self._stderr_tail else ""
                    raise MCPError(
                        f"MCP {self.connection.name} timed out waiting for {method}{detail}"
                    ) from exc
            finally:
                self._pending.pop(request_id, None)
        if isinstance(response, MCPError):
            raise response
        if "error" in response:
            raise MCPError(f"MCP {method} failed: {response['error']}")
        result = response.get("result")
        if not isinstance(result, dict):
            raise MCPError(f"MCP {method} returned no object result")
        return result

    def _write(self, message: dict[str, Any]) -> None:
        """以单行 UTF-8 JSON 写入服务，并用锁防止响应和请求文本交叉。"""
        process = self.connection.process
        if process is None or process.stdin is None or process.poll() is not None:
            raise MCPError(f"MCP server {self.connection.name} is not running")
        with self._write_lock:
            process.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
            process.stdin.flush()

    def close(self) -> None:
        """关闭当前服务并清空进程所有权，重复调用保持安全。

        读线程仍健康时先尝试协议 shutdown，随后终止并等待子进程；两秒内未退出
        则强制结束。协议关闭失败不会阻止操作系统级清理，最终始终把连接标记为
        未运行，避免 CLI 退出或握手失败后遗留受管进程。
        """
        process = self.connection.process
        if process is None:
            return
        try:
            if process.poll() is None:
                if self._reader_thread is not None and self._reader_thread.is_alive():
                    try:
                        self._request("shutdown", {})
                    except MCPError:
                        pass
                process.terminate()
                process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2)
        finally:
            self.connection.process = None
