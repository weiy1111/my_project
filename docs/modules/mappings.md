# 状态映射模块

源码：`src/auto_agent/mappings/status.py`

## 当前映射

| 内部状态 | Multica 状态 | IM 文案 |
| --- | --- | --- |
| `queued` | `queued` | 排队中 |
| `running` | `in_progress` | 执行中 |
| `completed` | `succeeded` | 已完成 |
| `failed` | `failed` | 执行失败 |
| `cancelled` | `cancelled` | 已取消 |
| `timed_out` | `failed` | 执行超时 |

状态转换集中在本模块，不能在 Worker 或 IM channel 中重复实现。若 Multica 实际协议使用不同状态名，只修改 gateway 映射，不修改内部状态枚举。
