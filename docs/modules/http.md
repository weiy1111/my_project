# HTTP 服务模块

源码：`src/auto_agent/http/app.py`

通过 `create_app(task_manager, multica_sink=None, components=None)` 创建 FastAPI 应用。

## 接口

| 方法 | 路径 | 作用 |
| --- | --- | --- |
| `GET` | `/healthz` | 存活检查 |
| `GET` | `/v1/agents` | 查询可用 Agent 摘要 |
| `GET` | `/v1/agents/{agent_name}` | 查询指定 Agent 摘要 |
| `POST` | `/v1/tasks` | 提交 Multica 任务 |
| `GET` | `/v1/tasks/{task_id}` | 查询任务上下文 |
| `GET` | `/v1/tasks/{task_id}/events` | 增量读取事件 |
| `DELETE` | `/v1/tasks/{task_id}` | 取消任务 |
| `POST` | `/v1/im/feishu/events` | 飞书事件订阅回调，启用渠道后注册 |

应用 lifespan 负责启动和关闭 Multica sink、IM components、TaskManager。HTTP 层只负责请求校验和异常转 HTTP 状态码，不包含任务执行逻辑。

Agent 查询接口不会返回 `system_prompt`、runtime 内部配置、沙箱配置或权限细节。

`create_app(..., webhook_channels=[channel])` 可以注册任意 `BaseWebhookImChannel`。回调完成验签和解析后立即返回，Agent 执行在后台进行，避免飞书因等待模型结果而超时重试。
