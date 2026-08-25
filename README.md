# auto_agent

`auto_agent` 是一个不绑定具体业务的通用桥接中间框架，用于连接上层任务调度平台、Hermes-Agent 等 Agent 执行引擎，以及可插拔的办公 IM 渠道。

当前开发版提供以下能力：

- 基于 Pydantic 的任务、执行参数、消息、上下文和事件模型。
- `BaseAgentWorker` 抽象，以及对接 Hermes-Agent ACP v1 的 `HermesACPWorker`。
- 由 Multica 和 IM 输入适配器共同使用的内存版 `TaskManager`。
- 基于 FastAPI 的任务提交、状态查询、事件查询、取消和健康检查接口。
- `BaseImChannel` 抽象，以及飞书 CLI、飞书开放平台 webhook 两种适配器。
- `ImTaskBridge`，用于完成 IM 消息到任务的转换、会话并发保护和有界消息去重。
- `HttpMulticaEventSink`，用于提供带鉴权、重试和退避机制的 Multica 回调。
- `AgentDefinition` 和 `AgentRegistry`，用于配置多个 Agent、权限策略和 Worker 路由。
- Python/CLI Skill 注册、强类型校验、超时执行和按 Agent 白名单生成临时 MCP Server。

Hermes 默认通过 stdio 上的 ACP v1 / JSON-RPC 2.0 接入，原 JSONL Worker 仅作为兼容模式保留。飞书开放平台通过独立 webhook 渠道接入，不侵入 TaskManager 和 Agent。框架不包含任何领域业务逻辑，也不提供自身持久化存储。

## 安装与运行

```bash
pip install -e '.[dev]'
auto-agent --config configs/auto_agent.yaml
```

默认配置已接入 `post_process_report` 使用的内部 MiMo Anthropic 兼容 API。启动前只需要在进程环境中设置凭据：

```bash
export MIMO_API_KEY='由内部密钥管理系统注入的值'
auto-agent --config configs/auto_agent.yaml
```

`auto_agent` 会为 Hermes 创建隔离的临时运行配置，把 `MIMO_API_KEY` 仅映射到子进程环境，不会把密钥写入 YAML。`configs/auto_agent.yaml` 已配置当前机器上的 `/home/mi/.local/bin/hermes-acp`。生产部署可改回 PATH 中的 `hermes-acp`，并由部署环境或密钥管理系统注入凭据。

飞书 CLI 模式示例位于 `examples/feishu_cli_mode.py`。飞书开放平台机器人示例位于 `examples/feishu_webhook_mode.py`，完整配置见 [飞书开放平台接入](docs/feishu_open_platform.md)。两个渠道可以独立启用或同时运行。

Agent 注册示例位于 `examples/agent_registry_mode.py`。任务可通过 `agent_name` 选择 Agent；未指定时使用 YAML 中配置的默认 Agent。

Skill 扩展示例和执行协议见 [Skill 扩展文档](docs/skills.md)，最小 Python Skill 工厂见 `examples/skills.py`。

已内置一个严格只读的 [代码审查 Agent](docs/agents/code_review.md)，调用名称为 `code_review`。

Hermes 的具体握手、会话、事件、取消和权限映射见 [Hermes ACP 协议接入](docs/hermes_acp_protocol.md)。

## 模块文档

各模块的职责、接口和扩展边界请参阅 [模块文档索引](docs/README.md)。

## 测试

在工作目录缓存不可写的环境中，可以使用以下命令运行测试：

```bash
PYTHONPATH=src pytest -q -o cache_dir=/tmp/auto-agent-pytest-cache
```
