import uvicorn

from auto_agent.gateways.multica import HttpMulticaEventSink
from auto_agent.http import create_app
from auto_agent.im_channels import FeishuCliChannel, ImTaskBridge
from auto_agent.task_manager import TaskManager
from auto_agent.workers import HermesACPWorker


manager = TaskManager(HermesACPWorker("hermes-acp"))
channel = FeishuCliChannel("feishu-cli", reconnect=True)
bridge = ImTaskBridge(channel, manager, workspace_id="default")
sink = HttpMulticaEventSink(default_callback_url=None)
app = create_app(manager, multica_sink=sink, components=[bridge])


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8080)
