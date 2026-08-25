# Agent 定义与注册模块

源码：`src/auto_agent/agents/`

## 职责

该模块用于声明、注册、选择和校验 Agent。它不执行模型推理，也不实现业务工具。

## AgentDefinition

| 字段 | 说明 |
| --- | --- |
| `name` | Agent 唯一名称，也是任务选择标识 |
| `description` | 面向调用方的用途说明 |
| `enabled` | 是否允许接收新任务 |
| `worker_name` | 对应的 Worker 路由名称 |
| `system_prompt` | Agent 系统提示词 |
| `runtime_config` | 模型和 runtime 配置 |
| `tools` | Agent 声明的工具及默认配置 |
| `memory_enabled` | 默认记忆开关 |
| `sandbox_config` | 默认沙箱配置 |
| `permission_policy` | 来源、工作区和覆盖权限 |

## AgentRegistry

主要接口：

- `register(definition)`：注册 Agent。
- `unregister(name)`：删除非默认 Agent。
- `get(name)`：获取 Agent 定义。
- `list()`：返回不含 system prompt 的安全摘要。
- `resolve(...)`：校验权限并合并任务级覆盖配置。

## 权限规则

`AgentPermissionPolicy` 支持限制：

- 允许的任务来源：Multica、IM。
- 允许的工作区。
- 是否允许覆盖工具、runtime、沙箱和记忆设置。
- 是否允许调用未在 Agent 中声明的工具。

严格 Agent 应将 `allow_unlisted_tools` 设置为 `false`。任务在权限校验失败时不会进入 TaskManager 队列。

## Worker 路由

`AgentDefinition.worker_name` 对应 TaskManager 构造参数中的 Worker 字典：

```python
manager = TaskManager(
    default_worker,
    agent_registry=registry,
    workers={"review-worker": review_worker},
)
```

同一个 Worker 可以被多个 Agent 复用，也可以为高权限或不同 runtime 的 Agent 配置独立 Worker。
