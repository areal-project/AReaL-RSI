from enum import Enum


class ModelServiceStatus(str, Enum):
    DEGRADED = "DEGRADED"
    FAILED = "FAILED"
    PARTIAL = "PARTIAL"
    PENDING = "PENDING"
    READY = "READY"
    STOPPED = "STOPPED"

    def __str__(self) -> str:
        return str(self.value)
