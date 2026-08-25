# 内部模型 API 接入

本框架已兼容 `post_process_report/llm/mimo_client.py` 使用的内部模型协议，但不依赖该业务模块。

## 协议契约

- 协议：Anthropic Messages API。
- 地址：`http://model.mify.ai.srv/anthropic/v1/messages`。
- 模型：`xiaomi/mimo-v2.5-pro`。
- 鉴权：HTTP `x-api-key`。Hermes Anthropic adapter 根据 `ANTHROPIC_API_KEY` 自动生成该请求头。
- 请求正文：`model`、`max_tokens`、`system`、`messages` 等 Anthropic 标准字段。

`auto_agent` 不复用报告项目的 `MimoClient` 类，因为该类是一次性文本生成客户端，不支持 Hermes 所需的工具调用、会话、事件和取消。框架只复用其模型协议契约，由 Hermes 负责模型交互。

## 凭据处理

运行服务前设置：

```bash
export MIMO_API_KEY='由内部密钥管理系统注入的值'
```

配置中的 `api_key_env: MIMO_API_KEY` 指定来源，`hermes_api_key_env: ANTHROPIC_API_KEY` 指定 Hermes 识别的目标变量。Worker 创建子进程时完成内存映射，不创建包含密钥的 `.env` 或 YAML。

不要从其他仓库复制硬编码 API key。`post_process_report` 当前源码存在默认 key，建议后续删除并统一改为必须从 `MIMO_API_KEY` 或公司密钥管理系统读取。

## Hermes 依赖

内部接口使用 Anthropic Messages 协议，Hermes 的虚拟环境需要安装兼容版本的 `anthropic` 包。当前机器已安装并通过真实调用验证。新环境如缺少依赖，可执行：

```bash
uv pip install --python /path/to/hermes/venv/bin/python 'anthropic>=0.39.0'
```

## 超时建议

内部服务延迟可能波动明显。最小健康请求曾接近 5 分钟，完整 Hermes 连通性请求也可能包含首次加载开销，因此：

- 保留 `hermes.default_timeout: 1800` 作为生产默认值。
- 不要把 HTTP/IM 入口的任务超时设为几十秒。
- 上层 Multica 的超时应覆盖排队、模型推理、工具执行和结果回调总时长。
- 超时后由 TaskManager 发送 `session/cancel` 并回收 Hermes 子进程。

## 联调结果

已完成一次真实端到端调用：`auto_agent -> hermes-acp -> MiMo Anthropic API`。Hermes session 创建成功，收到流式消息事件和一个 `final_output`，最终 `stop_reason=end_turn`。测试只记录事件数量和输出长度，没有打印响应正文或凭据。

## 调用链

```text
Multica / 飞书 / 库调用
          |
          v
TaskManager + AgentRegistry
          |
          v
HermesACPWorker
          |
          +-- 生成临时 Hermes model profile（不含密钥）
          +-- MIMO_API_KEY -> ANTHROPIC_API_KEY（仅子进程环境）
          |
          v
hermes-acp -> Anthropic adapter -> 内部 MiMo API
          |
          v
统一 AgentEvent -> Multica / 飞书 / HTTP
```
