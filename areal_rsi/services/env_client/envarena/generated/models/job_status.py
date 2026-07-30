from enum import Enum


class JobStatus(str, Enum):
    AGGREGATING = "AGGREGATING"
    CANCELLED = "CANCELLED"
    DONE = "DONE"
    FAILED = "FAILED"
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    STOPPED = "STOPPED"
    STOPPING = "STOPPING"
    TIMEOUT = "TIMEOUT"

    def __str__(self) -> str:
        return str(self.value)
