# 飞书开放平台接入

`FeishuWebhookChannel` 用于把飞书企业自建应用的机器人消息接入 `auto_agent`。群自定义机器人 webhook 主要用于单向发送，不能替代本模块需要的消息事件订阅。

## 飞书应用配置

1. 在飞书开放平台创建企业自建应用，并开启机器人能力。
2. 申请接收单聊/群聊消息和以机器人身份发送消息的权限。常见权限包括 `im:message`、`im:message:send_as_bot`、单聊消息读取和群聊 @ 消息读取，具体名称以租户控制台为准。
3. 在“事件与回调”中选择 HTTP 回调方式，订阅 `im.message.receive_v1`。
4. 配置请求地址：`https://你的域名/v1/im/feishu/events`。
5. 保存 Verification Token。生产环境建议同时设置 Encrypt Key。
6. 发布版本并由管理员安装应用，然后把机器人加入目标群。

回调地址必须能被飞书访问并使用有效 HTTPS 证书。服务会自动处理 URL verification 的 `challenge`。

## auto_agent 配置

编辑 `configs/auto_agent.yaml`：

```yaml
im_channels:
  feishu_webhook:
    enabled: true
    webhook_path: /v1/im/feishu/events
    base_url: https://open.feishu.cn
    workspace_id: default
    default_agent: general
    app_id_env: FEISHU_APP_ID
    app_secret_env: FEISHU_APP_SECRET
    verification_token_env: FEISHU_VERIFICATION_TOKEN
    encrypt_key_env: FEISHU_ENCRYPT_KEY
    group_session_scope: sender
    reply_mode: reply_message
    respond_to_group_mentions_only: true
    stream_events: false
    max_message_chars: 8000
    max_clock_skew_seconds: 300
```

`group_session_scope` 支持：

- `chat`：群内所有用户共享 Agent 会话。
- `sender`：按群和发送者隔离，默认且最适合普通聊天机器人。
- `thread`：按群话题隔离；没有 `root_id` 时使用当前消息 ID。

`reply_mode` 支持：

- `reply_message`：回复触发任务的原始飞书消息。
- `send_to_chat`：向当前会话发送普通消息。

## 凭据和启动

凭据只通过环境变量注入：

```bash
export FEISHU_APP_ID='应用 App ID'
export FEISHU_APP_SECRET='应用 App Secret'
export FEISHU_VERIFICATION_TOKEN='事件订阅 Verification Token'
export FEISHU_ENCRYPT_KEY='事件订阅 Encrypt Key'
export MIMO_API_KEY='内部模型凭据'

cd /home/mi/PycharmProjects/auto_agent
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src \
  python -m auto_agent.cli \
  --config configs/auto_agent.yaml \
  --host 0.0.0.0 \
  --port 8080
```

如果未在飞书控制台启用事件加密，把 YAML 中的 `encrypt_key_env` 改为 `null`，同时无需设置 `FEISHU_ENCRYPT_KEY`。

## 消息链路

```text
飞书事件订阅
  -> POST /v1/im/feishu/events
  -> 验签、解密、token 校验和消息转换
  -> 立即返回 HTTP 200
  -> ImTaskBridge
  -> TaskManager
  -> Hermes ACP
  -> MiMo 模型与工具
  -> 飞书回复消息 API
```

群聊默认只处理包含 @ 的文本消息，私聊文本直接处理。图片、文件、卡片等消息当前会被安全忽略，不会提交给 Agent。

## 运行边界

- 飞书事件 ID 去重、session 路由和任务状态当前都保存在进程内存中。
- 服务重启后不会恢复正在执行的 IM 任务；严格的跨重启幂等应由外部 Redis、Multica 或部署层提供。
- 回调入口快速返回，实际执行失败会通过飞书消息回复，不会让飞书 webhook 长时间阻塞。
- `stream_events` 默认关闭，避免 stdout 分片产生大量飞书消息。
- 生产环境应限制入口网络、使用 HTTPS、轮换 App Secret，并避免在日志中记录完整事件正文。
