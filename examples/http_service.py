import uvicorn

from auto_agent.http import create_app
from auto_agent.task_manager import TaskManager
from auto_agent.workers import FakeAgentWorker


app = create_app(TaskManager(FakeAgentWorker()))


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8080)
