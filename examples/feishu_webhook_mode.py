import os

import uvicorn

from auto_agent.http import create_app
from auto_agent.im_channels import FeishuWebhookChannel, ImTaskBridge
from auto_agent.task_manager import TaskManager
from auto_agent.workers import HermesACPWorker


manager = TaskManager(HermesACPWorker("hermes-acp"))
channel = FeishuWebhookChannel(
    app_id=os.environ["FEISHU_APP_ID"],
    app_secret=os.environ["FEISHU_APP_SECRET"],
    verification_token=os.environ["FEISHU_VERIFICATION_TOKEN"],
    encrypt_key=os.environ.get("FEISHU_ENCRYPT_KEY"),
)
bridge = ImTaskBridge(channel, manager, workspace_id="default", default_agent="general")
app = create_app(
    manager,
    components=[bridge],
    webhook_channels=[channel],
)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8080)
