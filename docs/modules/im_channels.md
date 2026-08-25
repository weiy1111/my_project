# IM 渠道模块

源码：`src/auto_agent/im_channels/`

## BaseImChannel

抽象接口包括：

- `connect()`
- `on_message(handler)`
- `send_reply(session_id, content)`
- `send_event(session_id, event)`
- `close()`

该层只做消息收发和格式转换，不执行 Agent，不决定任务策略。

## ImTaskBridge

负责把 `ImMessage` 转换为 `TaskManager.submit_im()`，并订阅任务事件回写 IM。它还提供：

- 同一 session 的执行中保护。
- 基于消息 ID 的有界去重。
- 任务完成、失败或取消后的 session 释放。

事件回复会携带当前输入消息的 `reply_callback`，避免同一会话在执行期间收到新消息后把旧任务结果回复到错误消息。

## BaseWebhookImChannel

在通用 IM 接口上增加：

- `webhook_path`：渠道的 HTTP 回调路径。
- `handle_webhook(body, headers)`：验签、解析并快速确认回调。

FastAPI 只依赖该抽象注册动态路由，因此后续钉钉、企微 webhook 不需要修改任务调度逻辑。

## FeishuCliChannel

当前约定飞书 CLI 使用 JSONL：输入 stdout 为消息，输出 stdin 为回复。实现包含连接监督、指数退避重连、消息解析和去重。真实飞书 CLI 字段需要在联调时通过 adapter 调整。

## FeishuWebhookChannel

对接飞书企业自建应用的事件订阅和机器人消息 API，包含：

- URL verification challenge。
- Verification Token 校验、签名校验和可选 AES-CBC 解密。
- `im.message.receive_v1` 文本消息转换。
- 群聊仅响应 @ 消息，并移除 prompt 中的机器人 mention 标记。
- 按群、发送者或话题生成 session ID。
- `tenant_access_token` 缓存和鉴权失败刷新。
- 回复原消息或发送到 chat 两种模式。
- 长文本分片、机器人消息过滤和后台异步分发。

默认不把 stdout 流式片段逐条发到飞书，防止产生大量消息；最终输出和错误仍会回复。完整部署步骤见 `docs/feishu_open_platform.md`。

新增钉钉或企业微信时，只需实现 `BaseImChannel` 或 `BaseWebhookImChannel`，主流程不需要改动。
