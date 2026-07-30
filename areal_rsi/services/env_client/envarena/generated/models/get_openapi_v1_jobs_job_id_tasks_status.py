from enum import Enum


class GetOpenapiV1JobsJobIdTasksStatus(str, Enum):
    ALL = "ALL"
    DONE = "DONE"
    FAILED = "FAILED"
    PENDING = "PENDING"
    RUNNING = "RUNNING"

    def __str__(self) -> str:
        return str(self.value)
