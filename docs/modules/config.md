# 配置模块

源码：`src/auto_agent/config.py`

## 职责

- 读取 YAML 配置。
- 使用 Pydantic 校验配置类型和范围。
- 为 Hermes、Multica 和 IM channel 提供统一配置模型。

## 配置模型

| 模型 | 主要字段 | 用途 |
| --- | --- | --- |
| `AgentsConfig` | `default_agent`, `definitions` | 默认 Agent 和 Agent 注册定义 |
| `HermesConfig` | `protocol`, `acp_command`, `model_provider`, `permission_mode`, `max_concurrency`, `default_timeout` | Hermes 协议、模型、进程权限和调度限制 |
| `MulticaConfig` | `callback_url`, `token`, `callback_retries` | Multica 出站回调 |
| `ImChannelConfig` | `enabled`, `command`, `webhook_path`, 凭据环境变量名、会话和回复策略 | IM CLI/webhook 生命周期 |
| `AutoAgentConfig` | `hermes`, `multica`, `im_channels` | 根配置 |

## 使用

```python
from auto_agent.config import load_config

config = load_config("configs/auto_agent.yaml")
print(config.hermes.default_timeout)
```

Token 建议通过环境变量或部署系统注入，不要提交到仓库。当前 YAML 读取不包含远程配置中心和热更新。

飞书开放平台配置只保存环境变量名称：

```yaml
im_channels:
  feishu_webhook:
    enabled: true
    webhook_path: /v1/im/feishu/events
    app_id_env: FEISHU_APP_ID
    app_secret_env: FEISHU_APP_SECRET
    verification_token_env: FEISHU_VERIFICATION_TOKEN
    encrypt_key_env: FEISHU_ENCRYPT_KEY
    group_session_scope: sender
    reply_mode: reply_message
```

服务启动时读取对应环境变量。声明了 `encrypt_key_env` 时该变量必须存在；如果飞书事件订阅未启用加密，应把该字段设为 `null`。

Hermes 默认配置含义：

- `protocol: acp`：使用官方 ACP v1；`jsonl` 只用于旧兼容程序。
- `acp_command` / `acp_args`：ACP 可执行文件及固定参数。
- `working_directory`：未被任务沙箱配置覆盖时的默认 cwd。
- `environment`：只放非敏感的进程环境覆盖；凭据优先由服务进程环境继承。
- `startup_timeout`：初始化、建会话和会话配置的总超时。
- `permission_mode`：`deny`、`allow_once` 或 `allow_session`；生产默认 `deny`。
- `model_provider`：声明 provider、模型、地址、协议模式和凭据环境变量映射。只能保存环境变量名，不能保存密钥值。

当前样例把 `post_process_report` 的内部 MiMo API 映射为 Hermes Anthropic provider：

```yaml
model_provider:
  provider: anthropic
  model: xiaomi/mimo-v2.5-pro
  base_url: http://model.mify.ai.srv/anthropic
  api_mode: anthropic_messages
  api_key_env: MIMO_API_KEY
  hermes_api_key_env: ANTHROPIC_API_KEY
```

Worker 启动时读取 `MIMO_API_KEY`，在独立 Hermes 子进程中映射为 `ANTHROPIC_API_KEY`，并生成不含密钥的临时 Hermes 配置。缺少环境变量时任务会在启动 Hermes 前失败；Worker 关闭时临时配置会被删除。

Agent 可在 YAML 的 `agents.definitions` 中声明。生产环境应显式设置权限策略，不要依赖宽松默认值。
