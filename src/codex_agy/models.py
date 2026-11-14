from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class State(StrEnum):
    CREATED = "CREATED"
    VALIDATED = "VALIDATED"
    QUEUED = "QUEUED"
    STARTING = "STARTING"
    RUNNING = "RUNNING"
    WAITING_PERMISSION = "WAITING_PERMISSION"
    WAITING_INPUT = "WAITING_INPUT"
    VERIFYING = "VERIFYING"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    FIX_REQUESTED = "FIX_REQUESTED"
    ACCEPTED = "ACCEPTED"
    FAILED = "FAILED"
    TIMED_OUT = "TIMED_OUT"
    CANCELLED = "CANCELLED"
    BLOCKED = "BLOCKED"
    INTERRUPTED = "INTERRUPTED"


ACTIVE = {
    State.STARTING,
    State.RUNNING,
    State.WAITING_PERMISSION,
    State.WAITING_INPUT,
    State.VERIFYING,
}
BUSY = ACTIVE | {State.QUEUED, State.FIX_REQUESTED}


class Problem(Exception):
    def __init__(self, code: str, message: str, details=None):
        super().__init__(message)
        self.code = code
        self.details = details

    def payload(self) -> dict:
        return {"code": self.code, "message": str(self), "details": self.details}
