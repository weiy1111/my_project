from enum import Enum


class StrEnum(str, Enum):
    """Backport of Python 3.11's StrEnum for Python 3.10 deployments."""

    def __str__(self) -> str:
        return self.value


class TaskSource(StrEnum):
    MULTICA = "multica"
    IM = "im"


class TaskStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"


class EventType(StrEnum):
    STDOUT = "stdout"
    STDERR = "stderr"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    STATUS_CHANGED = "status_changed"
    ERROR = "error"
    FINAL_OUTPUT = "final_output"
