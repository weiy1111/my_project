from auto_agent.models import TaskStatus


MULTICA_STATUS = {
    TaskStatus.QUEUED: "queued",
    TaskStatus.RUNNING: "in_progress",
    TaskStatus.COMPLETED: "succeeded",
    TaskStatus.FAILED: "failed",
    TaskStatus.CANCELLED: "cancelled",
    TaskStatus.TIMED_OUT: "failed",
}

IM_STATUS_TEXT = {
    TaskStatus.QUEUED: "排队中",
    TaskStatus.RUNNING: "执行中",
    TaskStatus.COMPLETED: "已完成",
    TaskStatus.FAILED: "执行失败",
    TaskStatus.CANCELLED: "已取消",
    TaskStatus.TIMED_OUT: "执行超时",
}


def to_multica_status(status: TaskStatus) -> str:
    return MULTICA_STATUS[status]


def to_im_status_text(status: TaskStatus) -> str:
    return IM_STATUS_TEXT[status]
