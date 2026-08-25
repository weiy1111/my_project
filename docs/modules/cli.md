# CLI 与运行模式

源码：`src/auto_agent/cli.py`

## 独立服务模式

```bash
auto-agent --config configs/auto_agent.yaml --host 127.0.0.1 --port 8080
```

CLI 根据配置创建 Hermes Worker、TaskManager、Multica sink 和启用的 IM channel，然后启动 Uvicorn。

## 库模式

```python
from auto_agent.task_manager import TaskManager
from auto_agent.workers import FakeAgentWorker

manager = TaskManager(FakeAgentWorker())
```

更完整示例见 `examples/library_mode.py`、`examples/agent_registry_mode.py`、`examples/http_service.py` 和 `examples/feishu_cli_mode.py`。库模式的生命周期由调用方负责，服务模式由 FastAPI lifespan 负责。
