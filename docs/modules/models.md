# 数据模型模块

源码：`src/auto_agent/models/`

## 核心模型

- `MulticaTaskRequest`：上层调度提交的任务。
- `HermesExecutionRequest`：Worker 执行参数。
- `ImMessage`：IM 通道统一输入消息。
- `AgentEvent`：统一事件结构，包含事件类型、序号、时间戳和 payload。
- `TaskContext`：任务运行时上下文和生命周期状态。
- `TaskResult`：任务结果摘要。

## 设计原则

模型只表达协议数据，不执行任务，不调用外部服务。`extra="forbid"` 用于核心任务模型，避免上游拼写错误静默进入执行链路；IM 消息保留扩展字段，方便不同渠道携带原始元数据。

所有事件必须携带 `task_id`。跨系统追踪使用 `request_id`、`im_session_id` 和 `hermes_session_id`。

Multica 和 IM 消息均支持 `agent_name`。最终选中的 Agent 和 Worker 会记录在 `TaskContext.agent_name`、`TaskContext.worker_name` 中。
