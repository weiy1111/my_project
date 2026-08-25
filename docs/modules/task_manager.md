# 任务管理器模块（TaskManager）

源码：`src/auto_agent/task_manager/manager.py`

## 职责

TaskManager 是唯一的任务调度中枢，统一接收 Multica 和 IM 两种来源，维护进程内任务上下文，并把 Worker 事件发送给订阅者。

## 主要能力

- 双来源提交：`submit_multica()`、`submit_im()`。
- Agent 解析：入队前通过 `AgentRegistry` 选择 Agent 并校验权限。
- Worker 路由：按照 Agent 的 `worker_name` 选择执行器。
- 并发限制：基于 `asyncio.Semaphore`。
- 生命周期：queued、running、completed、failed、cancelled、timed_out。
- 超时和取消：调用 Worker 终止执行。
- 任务幂等：同一个 `task_id` 重复提交会抛出 `DuplicateTaskError`。
- 事件订阅：单任务 `subscribe()`，全局 `subscribe_all()`。
- 事件拉取：`events(task_id, after_sequence=N)`。

## 状态边界

TaskManager 不写数据库，服务重启后内存任务和事件会丢失。需要恢复时，应由上层调度平台重新投递任务或接入独立状态存储层，而不是在 TaskManager 内部偷偷增加持久化。
