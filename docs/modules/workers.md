# 执行器适配器模块（Worker）

源码：`src/auto_agent/workers/`

## 抽象接口

`BaseAgentWorker` 定义四个异步操作：

- `start_task(task_id, request)`
- `cancel_task(task_id)`
- `get_status(task_id)`
- `stream_events(task_id)`

Worker 只负责调用 Agent runtime，不知道 Multica、飞书或业务领域。

## HermesACPWorker

默认 Worker 通过 `asyncio.create_subprocess_exec` 启动 `hermes-acp`，使用 ACP v1 的换行分隔 JSON-RPC 2.0 协议：

- 启动后发送 `initialize`，校验 `protocolVersion=1`。
- 每个任务创建或加载 Hermes session，再发送 `session/prompt`。
- `session/update` 中的消息和工具事件转换为统一 `AgentEvent`。
- `session/cancel` 负责运行中取消，关闭时回收子进程。
- JSON-RPC error、协议断开和非正常退出转换为 Hermes 异常体系。
- Hermes 反向发送的权限请求默认拒绝，只有显式配置才允许。

一个运行中的任务使用一个 ACP 子进程，避免不同 Agent 的 cwd、凭据和权限配置相互污染。`memory_enabled=true` 时，Worker 会记住逻辑 session 与 Hermes session 的映射，并在后续任务中调用 `session/load`。Hermes 自身会保存 ACP session；`auto_agent` 不额外持久化映射，服务重启后的会话恢复仍需要上层提供扩展实现。

## HermesAgentWorker

这是早期 JSONL 子进程适配器，仅在配置 `hermes.protocol: jsonl` 时使用。它适用于自定义的 Hermes 兼容包装程序，不代表 NousResearch Hermes-Agent 的官方协议。

`FakeAgentWorker` 仅用于测试和示例。
