# 测试与验证模块

测试目录：`tests/`

## 当前覆盖

- Fake Worker 完成和重复任务。
- FastAPI 路由存在性。
- Multica callback 结构化 payload 和 401 行为。
- IM 消息去重、session 保护和事件回写。
- Hermes JSONL stdout、stderr 和成功退出。
- Hermes ACP v1 初始化、建会话、会话复用、prompt 和 camelCase 字段。
- ACP 消息、工具开始/完成、最终输出和 usage 事件映射。
- ACP 权限默认拒绝、任务取消、JSON-RPC error 和协议版本不兼容。
- Agent 注册、默认选择、权限校验、配置合并和 Worker 路由。
- Ruff 静态检查和格式检查。

## 本地运行

```bash
PYTHONPATH=src pytest -q -o cache_dir=/tmp/auto-agent-pytest-cache
RUFF_CACHE_DIR=/tmp/auto-agent-ruff-cache ruff check src tests examples
RUFF_CACHE_DIR=/tmp/auto-agent-ruff-cache ruff format --check src tests examples
```

已使用本机 Hermes-Agent v0.20.5 和内部 MiMo API 完成真实端到端验证：ACP 初始化、session 创建、模型流式消息和 `final_output` 均成功，`stop_reason=end_turn`。测试未打印响应正文或凭据。

飞书 webhook 单元测试覆盖 URL 校验、Verification Token、签名、AES 加密消息、异步分发、群聊 @ 过滤、session 映射、tenant token 缓存和回复原消息。真实飞书应用权限、外网 HTTPS 回调和平台限频仍需要在目标租户联调验证。
