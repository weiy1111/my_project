# auto_agent 模块文档

这些文档描述当前源码中的模块职责、公开接口和扩展边界。框架只负责协议桥接，不包含任何业务逻辑、持久化或具体 Agent 推理实现。

## 模块索引

- [配置层](modules/config.md)
- [Agent 定义与注册](modules/agents.md)
- [数据模型层](modules/models.md)
- [状态映射](modules/mappings.md)
- [异常体系](modules/exceptions.md)
- [执行器适配器（Worker）](modules/workers.md)
- [Hermes ACP 协议接入](hermes_acp_protocol.md)
- [内部模型 API 接入](internal_model_api.md)
- [任务管理器（TaskManager）](modules/task_manager.md)
- [Multica 网关](modules/gateways_multica.md)
- [IM 渠道](modules/im_channels.md)
- [飞书开放平台接入](feishu_open_platform.md)
- [HTTP 服务](modules/http.md)
- [CLI 与运行模式](modules/cli.md)
- [测试与验证](modules/testing.md)

## Agent 目录

- [代码审查 Agent](agents/code_review.md)

## 通用数据流

```text
Multica HTTP / IM 渠道
          |
          v
      输入适配器 / 桥接器
          |
          v
      TaskManager
          |
          v
   BaseAgentWorker
          |
          v
      Hermes 执行引擎
          |
          v
  事件出口：Multica + IM
```

## 开发约束

1. 新增业务不得进入 `auto_agent`。
2. 外部平台协议只能放在 gateway、worker 或 channel 适配器中。
3. TaskManager 不直接访问 HTTP、CLI 或飞书 API。
4. 当前状态存储是进程内内存，不提供持久化保证。
