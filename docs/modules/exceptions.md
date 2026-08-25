# 异常模块

源码：`src/auto_agent/exceptions.py`

## 异常分类

- `MulticaProtocolError`：Multica 回调协议或网络错误。
- `MulticaAuthError`：Multica 返回 401。
- `MulticaRejectedError`：不可重试的 4xx 拒绝。
- `ImChannelError`：IM 连接、消息解析或发送失败。
- `HermesExecutionError`：Worker 执行失败。
- `HermesProcessCrashError`：Hermes 非零退出。
- `TaskTimeoutError`：超过任务超时。
- `TaskCancelledError`：任务被取消。
- `DuplicateTaskError`、`TaskNotFoundError`：TaskManager 状态错误。

每个异常提供 `code`、`message`、`retryable` 和结构化 `context`。对外回调和日志应使用 `as_dict()`，避免直接暴露 Python traceback 给终端用户。
