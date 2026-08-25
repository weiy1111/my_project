# 代码审查 Agent

Agent 名称：`code_review`

## 定位

`code_review` 是一个通用、只读的代码审查 Agent。它负责发现具体缺陷、行为回归、安全风险、异常路径问题和测试缺口，不负责修改代码。

## 审查输出

审查结果遵循以下顺序：

1. `Findings`：按 critical、high、medium、low 排序，每条包含文件行号、触发条件、影响和修复建议。
2. `Open questions`：影响结论的未决问题。
3. `Test gaps`：缺失或未执行的验证。
4. `Summary`：审查范围和总体风险。

没有发现问题时，Agent 会明确说明“未发现需要阻塞合入的问题”，不会为了填充输出而制造问题。

## 权限边界

- 允许读取仓库文件、搜索文本和查看 Git 状态、diff、提交与日志。
- 允许在临时写入范围内执行选定测试、lint、类型检查和格式检查。
- 禁止编辑仓库文件、提交、推送、安装依赖和访问网络。
- 禁止任务覆盖工具、runtime、沙箱和记忆配置。
- 禁止调用 Agent 未声明的工具。
- 不保留跨任务记忆，避免不同仓库或审查任务之间串数据。

工具名称是 `auto_agent` 的逻辑契约。在 Hermes ACP 中按以下方式落地：

| auto_agent 逻辑能力 | Hermes 实际能力 |
| --- | --- |
| `repository_read` | `file` 工具集中的读取、文件搜索和文本搜索 |
| `git_read` | `terminal` 执行只读 Git 命令 |
| `test_runner` | `terminal` 执行上层允许的测试命令 |
| `static_analysis` | `terminal` 执行 lint、类型和格式检查命令 |

Hermes ACP v1 不提供“按 session 只开放四个逻辑工具”的字段，实际 session 使用 Hermes 的 `hermes-acp` toolset。`auto_agent` 通过 system prompt、cwd 和默认拒绝 ACP 权限请求限制行为；生产环境还必须使用只读挂载或容器沙箱实施强制边界。

## Multica 调用

```bash
curl -X POST http://127.0.0.1:8080/v1/tasks \
  -H 'Content-Type: application/json' \
  --data @examples/code_review_task.json
```

查询状态和事件：

```bash
curl http://127.0.0.1:8080/v1/tasks/review-example-001
curl 'http://127.0.0.1:8080/v1/tasks/review-example-001/events?after_sequence=-1'
```

## 飞书 CLI 调用

飞书 CLI 消息需要提供以下标准字段：

```json
{
  "channel_id": "feishu_cli",
  "session_id": "review-session-001",
  "sender": "user-id",
  "agent_name": "code_review",
  "content": "审查当前工作区改动，重点看异常处理和测试缺口。",
  "metadata": {
    "message_id": "message-001"
  }
}
```

## 当前限制

Agent 定义、权限校验、任务路由、Hermes ACP 协议和事件链路已经完成。当前机器还没有配置 Hermes 模型 provider/API key，因此尚未进行真实模型代码审查。严格只读部署还需要调用方提供容器或文件系统级沙箱，ACP 权限回调本身不能替代操作系统隔离。
