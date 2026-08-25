class AutoAgentError(Exception):
    """Base error with structured context suitable for logs and callbacks."""

    code = "auto_agent_error"
    retryable = False

    def __init__(self, message: str, *, context: dict | None = None):
        super().__init__(message)
        self.message = message
        self.context = context or {}

    def as_dict(self) -> dict:
        return {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
            "context": self.context,
        }


class TaskNotFoundError(AutoAgentError):
    code = "task_not_found"


class DuplicateTaskError(AutoAgentError):
    code = "duplicate_task"


class AgentNotFoundError(AutoAgentError):
    code = "agent_not_found"


class AgentDisabledError(AutoAgentError):
    code = "agent_disabled"


class DuplicateAgentError(AutoAgentError):
    code = "duplicate_agent"


class AgentPolicyError(AutoAgentError):
    code = "agent_policy_denied"


class AgentWorkerNotFoundError(AutoAgentError):
    code = "agent_worker_not_found"


class TaskTimeoutError(AutoAgentError):
    code = "task_timeout"


class TaskCancelledError(AutoAgentError):
    code = "task_cancelled"


class HermesExecutionError(AutoAgentError):
    code = "hermes_execution_error"


class HermesProcessCrashError(HermesExecutionError):
    code = "hermes_process_crash"


class MulticaProtocolError(AutoAgentError):
    code = "multica_protocol_error"
    retryable = True


class MulticaRejectedError(MulticaProtocolError):
    code = "multica_rejected"
    retryable = False


class MulticaAuthError(MulticaProtocolError):
    code = "multica_auth_error"
    retryable = False


class ImChannelError(AutoAgentError):
    code = "im_channel_error"
    retryable = True


class ImChannelAuthError(ImChannelError):
    code = "im_channel_auth_error"
    retryable = False


class ImChannelProtocolError(ImChannelError):
    code = "im_channel_protocol_error"
    retryable = False
