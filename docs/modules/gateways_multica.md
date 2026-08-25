# Multica 网关模块

源码：`src/auto_agent/gateways/multica/`

## MulticaEventSink

`MulticaEventSink` 是出站回调抽象：

- `publish_event(event, context)`：发送 stdout、工具调用、错误、最终输出等事件。
- `publish_status(context)`：发送任务状态变更。

## HttpMulticaEventSink

当前 HTTP 实现具备：

- Bearer token 鉴权。
- `401` 转为 `MulticaAuthError`，不重试。
- `429`、`5xx` 支持有限次指数退避。
- 其他 `4xx` 转为不可重试的 `MulticaRejectedError`。
- payload 带 `schema_version`、`task_id`、`workspace_id`、`request_id` 和嵌套事件。

真实 Multica 字段、签名和 endpoint 仍需按平台协议联调确认。协议变化应限制在本目录。
