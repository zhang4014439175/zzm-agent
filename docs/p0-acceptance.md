# P0 阶段验收说明

本文档记录 P0「ReAct 可靠性与评测」阶段验收结果。P0 覆盖 5.1 到 5.4，目标是让现有单轮 ReAct 循环具备更可靠的停滞检测、一次性纠偏、工具错误恢复和确定性回放基准。

## 验收结论

P0 阶段验收通过。

当前已完成：

- 5.1 ProgressMonitor 无进展检测；
- 5.2 一次性 Reflection 纠偏；
- 5.3 工具错误恢复增强；
- 5.4 回放基准扩充。

当前下一阶段：

```text
P1：Conversation Runtime 与完整状态管理
```

## P0 执行链路总览

P0 形成的完整链路是：

```text
AgentLoop.run()
→ 模型产生 tool_calls
→ AgentLoop 执行工具
→ 工具结果转为 ToolObservation
→ ProgressMonitor.observe_round()
→ 如果无进展，生成 ProgressSignal
→ AgentLoop 注入一次 REFLECTION_REQUIRED
→ 如果仍无进展，安全停止

工具异常
→ tool_error_from_exception()
→ ToolError 分类、确定性标记、Retry-After、attempts
→ AgentLoop 根据 retryable / deterministic 决定是否重试
→ 最终结构化 Observation 返回给模型

Replay Benchmark
→ ReplayLLM + MockToolRegistry
→ 固定工具结果或异常
→ 扩展 expected 断言
→ 验证 Reflection、错误分类、Retry-After 和安全停止
```

## 验收项 1：相同失败调用不会持续到最大迭代次数

对应代码：

- `zzm_agent/core/progress_monitor.py`
  - `ProgressMonitor.observe_round()`
  - `ProgressSignal`
  - `ToolObservation`
- `zzm_agent/core/agent_loop.py`
  - `AgentLoop.run()`
  - `AgentLoop._request_reflection()`
  - `AgentLoop._no_progress_stop_message()`
- `zzm_agent/core/errors.py`
  - `tool_error_from_exception()`
  - `ToolError`

验收说明：

P0 不再只依赖 `max_tool_iterations` 兜底。现在会提前识别以下停滞模式：

- 参数变化但 Observation 重复；
- 连续不可重试失败；
- 多工具固定循环；
- 重复相同工具调用；
- 确定性工具错误。

验证位置：

- `tests/test_progress_monitor.py`
- `tests/test_agent_loop.py`
- `tests/test_tool_error_recovery.py`
- `zzm_agent/eval/benchmarks/07_reflection_repeated_observation.yaml`
- `zzm_agent/eval/benchmarks/08_error_category_recovery.yaml`

验收结果：

通过。目标测试覆盖了重复 Observation、不可重试失败、固定循环、确定性参数错误和错误分类恢复。

## 验收项 2：Reflection 不绕过权限确认和硬性熔断

对应代码：

- `zzm_agent/core/agent_loop.py`
  - `AgentLoop._reflection_prompt()`
  - `AgentLoop._request_reflection()`
  - `self.last_reflection_count`
  - `max_tool_iterations`
  - `duplicate_tool_call_limit`
  - `_is_tool_execution_approved()`

验收说明：

Reflection 是一次性纠偏，不是新的无限循环入口。它满足：

- 每个用户 Turn 最多触发一次；
- 不重置 `max_tool_iterations`；
- 不绕过工具权限确认；
- Reflection 后仍无进展会停止；
- runtime-only，不写入持久历史。

验证位置：

- `tests/test_agent_loop.py`
- `tests/test_agent_replay.py`
- `zzm_agent/eval/benchmarks/07_reflection_repeated_observation.yaml`

验收结果：

通过。Replay benchmark 能检查 `reflection_count=1`、`progress_reason=repeated_observation` 和运行时请求中包含 `REFLECTION_REQUIRED`。

## 验收项 3：正常短任务的调用次数和延迟无明显退化

对应代码：

- `zzm_agent/core/agent_loop.py`
  - 正常无工具回复路径；
  - 正常工具调用路径；
  - `ProgressMonitor` 仅在工具轮结束后处理 Observation；
  - Reflection 仅在停滞信号出现后触发。

验收说明：

P0 的新增机制不会让正常短任务默认增加额外模型调用：

- 没有工具调用时，不触发 ProgressMonitor；
- 正常工具调用成功时，不触发 Reflection；
- 只有可重试工具错误才进入有界重试；
- 确定性错误不会盲目重试。

验证位置：

- `tests/test_progress_monitor.py`
- `tests/test_tool_error_recovery.py`
- `tests/test_eval_runner.py`
- `zzm_agent/eval/benchmarks/01_code_reading.yaml`
- `zzm_agent/eval/benchmarks/02_file_edit.yaml`

验收结果：

通过。Replay suite 中正常基准仍能通过，新增 Reflection 和错误恢复断言没有破坏既有 replay 场景。

## 验收项 4：新增行为均具备确定性回放测试

对应代码：

- `zzm_agent/eval/runner.py`
  - `_ReplayMemoryStore`
  - `_run_replay()`
  - `_exception_from_mock()`
  - `_check_extended_replay_expectations()`
- `zzm_agent/eval/replay.py`
  - `ReplayLLM`
  - `ReplayTurn`
  - `ReplayToolCall`
  - `MockToolRegistry`

新增回放基准：

- `zzm_agent/eval/benchmarks/07_reflection_repeated_observation.yaml`
- `zzm_agent/eval/benchmarks/08_error_category_recovery.yaml`
- `zzm_agent/eval/benchmarks/09_retry_after_external_service.yaml`

验收说明：

5.4 扩展了 replay expected 能力，可以检查：

- 模型调用次数；
- Reflection 次数；
- ProgressSignal 原因；
- 运行时 Prompt 内容；
- 工具调用顺序；
- Retry-After 等待；
- 工具结果 JSON 字段。

验收结果：

通过。完整 replay suite 当前 9/9 通过。

## 本次验证命令

目标测试：

```text
pytest tests\test_progress_monitor.py tests\test_tool_error_recovery.py tests\test_eval_runner.py -q -p no:cacheprovider --tb=short
```

结果：

```text
12 passed in 0.69s
```

Replay suite：

```text
python -B -c "from zzm_agent.eval.runner import run_eval; raise SystemExit(run_eval('replay', False, {'model': {}, 'agent': {}}))"
```

结果：

```text
Eval completed: 9/9 passed.
Metrics: Success Rate: 100.0%, Tool Calls Evaluated: 9
```

格式检查：

```text
git diff --check
```

结果：

通过，仅存在 Git 的 LF/CRLF 换行提示。

## 验收期间发现并修复的问题

### Replay eval 不应依赖系统临时目录

问题：

`run_eval('replay')` 原先会为每个 benchmark 创建临时 workspace，并写入 `initial_files`。当前 Windows 环境对系统临时目录和部分项目临时目录存在写入/清理权限限制，导致 replay suite 无法稳定运行。

修复：

- Replay 模式改为不创建真实 workspace；
- Replay 模式使用 `_ReplayMemoryStore`；
- Replay 工具结果完全来自 YAML `mock_tool_results`；
- LLM suite 仍保留真实 workspace 逻辑。

对应代码：

- `zzm_agent/eval/runner.py`
  - `run_eval()`
  - `_ReplayMemoryStore`

## 已知限制

- 当前环境中仍有历史遗留的 `__pycache__` 脏文件和若干权限受限临时目录；
- 部分旧测试使用 pytest `tmp_path`，可能在 Windows 临时目录 fixture setup 阶段被权限阻塞；
- 这些限制不影响 P0 新增目标测试和 replay suite 的验收结果。

## 后续进入 P1 的迁移点

进入 P1 后，应将 P0 中临时保存在 `AgentLoop` 上的运行状态迁移到正式状态模型：

- `last_progress_signal` → `LoopState.progress_signal`
- `last_reflection_count` → `LoopState.reflection_count`
- Reflection 触发原因 → `LoopTransition`
- 工具 Observation → `LoopState.observations`
- Replay/Benchmark 结果 → EventBus / Checkpoint / Artifact 体系

P0 的职责到此完成：它让现有单轮 ReAct 循环具备了可靠性闭环；P1 将在此基础上建设完整 Conversation Runtime。
