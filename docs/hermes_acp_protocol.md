# Hermes ACP 协议接入

`auto_agent` 默认通过 Hermes-Agent v0.20.5 提供的 Agent Client Protocol（ACP）接入执行引擎。传输层是 stdio，每行一个 JSON-RPC 2.0 对象，协议版本为 1。

## 生命周期映射

| auto_agent 操作 | Hermes ACP 方法 | 说明 |
| --- | --- | --- |
| Worker 启动 | `initialize` | 发送客户端能力并校验 `protocolVersion=1` |
| 新任务会话 | `session/new` | 传入绝对路径 `cwd` 和可选 `mcpServers` |
| 延续 IM 会话 | `session/load` | 使用内存中的逻辑 session 到 Hermes session 映射 |
| 选择模型 | `session/set_model` | `agent_config.model` 存在时发送 |
| 选择执行模式 | `session/set_mode` | `agent_config.mode` 存在时发送 |
| 执行任务 | `session/prompt` | prompt 使用 ACP text content block |
| 取消任务 | `session/cancel` | notification，无响应体 |
| 无记忆任务清理 | `session/close` | `memory_enabled=false` 时关闭 Hermes session |

ACP 使用 camelCase 字段，例如 `protocolVersion`、`sessionId`、`mcpServers`、`stopReason` 和 `sessionUpdate`。Worker 对外仍使用框架自己的 snake_case Pydantic 模型。

## Prompt 和配置映射

- `HermesExecutionRequest.prompt` 映射为 `session/prompt.prompt[0].text`。
- `agent_config.system_prompt` 以明确的指令边界前置到用户 prompt。Hermes ACP v1 没有单独的 system prompt 字段。
- `agent_config.model` 映射为 `session/set_model.modelId`。
- `agent_config.mode` 映射为 `session/set_mode.modeId`。
- `agent_config.mcp_servers` 原样映射为 ACP `mcpServers`，调用方必须提供符合 ACP schema 的对象。
- `sandbox_config.cwd` 或 `sandbox_config.workspace_path` 映射为 ACP session cwd。
- `memory_enabled=true` 允许同一逻辑 session 在当前服务生命周期内调用 `session/load`；为 false 时执行完调用 `session/close`。

Agent 声明中的 `tools` 是 `auto_agent` 的权限契约，不会伪装成 Hermes 不存在的工具名。Hermes ACP 当前固定暴露 `hermes-acp` toolset；如需接入自定义工具，应通过 `agent_config.mcp_servers` 使用 MCP。代码审查 Agent 的 `repository_read`、`git_read`、`test_runner`、`static_analysis` 是逻辑能力分类，由 system prompt、cwd、ACP 权限回调和部署沙箱共同约束。

## 事件映射

| ACP `sessionUpdate` | `AgentEvent.event_type` | 说明 |
| --- | --- | --- |
| `agent_message_chunk` | `stdout` | `payload.stream=agent_message`，同时聚合最终文本 |
| `agent_thought_chunk` | `stdout` | `payload.stream=agent_thought` |
| `tool_call` | `tool_call` | 工具开始事件 |
| `tool_call_update`，运行中 | `tool_call` | 工具进度事件 |
| `tool_call_update`，完成或失败 | `tool_result` | 工具结束事件 |
| `session/prompt` 响应 | `final_output` | 包含聚合文本、`stop_reason`、usage 和 Hermes session ID |

`stopReason=cancelled` 映射为取消，`refusal` 映射为执行失败，JSON-RPC error 和进程断开统一转换为 `HermesExecutionError` 或 `HermesProcessCrashError`。

## 权限策略

Hermes 可能通过 `session/request_permission` 反向请求危险命令或文件编辑授权。默认 `permission_mode: deny`，Worker 从服务端提供的选项中选择 `deny`；找不到拒绝选项时返回 `cancelled`。`allow_once` 和 `allow_session` 只能在部署侧已经存在真实文件系统、网络和进程沙箱时使用。

ACP 权限回调不是操作系统沙箱。严格只读任务仍应在容器、只读挂载或同等级隔离环境中运行；仅依赖 prompt 或权限回调不能证明任意命令绝对只读。

## 联调前置条件

默认配置已接入内部 MiMo Anthropic Messages API，只需通过环境变量注入凭据：

```bash
export MIMO_API_KEY='由内部密钥管理系统注入的值'
```

Worker 会在临时 `HERMES_HOME` 中生成以下运行配置：provider 为 `anthropic`，模型为 `xiaomi/mimo-v2.5-pro`，base URL 为内部 `/anthropic` 代理，API mode 为 `anthropic_messages`。`MIMO_API_KEY` 只在子进程环境中映射为 `ANTHROPIC_API_KEY`，不会写入配置文件或日志。

如需切换其他模型服务，只修改 `hermes.model_provider`。API key 必须通过进程环境提供，禁止写入项目 YAML。
