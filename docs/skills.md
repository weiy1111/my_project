# Skill 扩展

Skill 是 Agent 调用外部代码的统一能力层。`auto_agent` 负责注册、白名单、输入输出校验、超时、并发和事件；具体业务代码留在外部包或业务仓库。

## 支持方式

### Python Skill

适合同一 Python 环境中的可信函数。函数工厂必须返回 `BaseSkill`，输入和输出使用 Pydantic 模型。完整示例见 `examples/skills.py`。

```yaml
skills:
  enabled: true
  definitions:
    - provider: python
      name: add_numbers
      factory: examples.skills:add_numbers_skill
      timeout: 30
```

### CLI Skill

适合已有脚本或需要独立进程隔离的代码。框架使用固定 argv 启动命令，不经过 shell。stdin 输入包含 `arguments` 和任务 `context`，CLI 需要向 stdout 输出 JSON。

```yaml
skills:
  enabled: true
  definitions:
    - provider: cli
      name: generate_report
      description: 生成报告
      command: [python, /opt/report_skills/generate.py]
      cwd: /opt/report_skills
      timeout: 1800
      input_schema:
        type: object
        properties:
          project_id: {type: integer}
        required: [project_id]
        additionalProperties: false
      output_schema:
        type: object
        properties:
          status: {type: string}
          report_url: {type: [string, "null"]}
        required: [status, report_url]
      secret_env:
        INTERNAL_API_KEY: REPORT_API_KEY
```

`secret_env` 的值是环境变量名，不是密钥值。

## Agent 白名单和 MCP

只有注册且被 Agent `tools` 字段声明的 Skill 才会进入当前 Hermes session：

```yaml
agents:
  definitions:
    - name: report_agent
      tools:
        generate_report: {}
      permission_policy:
        allow_unlisted_tools: false
```

TaskManager 会为每个任务生成临时 stdio MCP Server 描述，并通过 ACP `session/new` 的 `mcpServers` 传给 Hermes。Hermes 自动发现工具，模型决定是否调用，调用结果再经过 Skill Executor 校验。

```text
Agent tools 白名单 -> 临时 MCP Server -> Hermes 工具发现 -> Python/CLI Skill -> 结果校验 -> 最终回答
```

HTTP 接口：`GET /v1/skills`。它只返回 Skill 名称、描述、provider、schema 和超时，不返回密钥值。

## 安全边界

- CLI 不接受任意用户命令，不使用 `shell=True`。
- Agent 只能调用 `tools` 中明确授权的 Skill。
- Python Skill 适用于可信代码；不可信代码必须使用 CLI 加容器或沙箱。
- 每次调用都有超时、取消、并发限制和 started/completed/failed 事件。
- 输入输出严格经过 Pydantic 或 JSON Schema 校验。
- 环境变量只按白名单传递，密钥不写入任务、YAML 或事件日志。
- 有副作用的 Skill 应由业务侧实现幂等键和人工确认。

## 独立 MCP 入口

```bash
auto-agent-skills --config configs/auto_agent.yaml --allow generate_report
```

通常不需要手工运行；HTTP 服务会在每个 Agent 任务中为授权 Skill 自动启动临时 MCP 进程。
