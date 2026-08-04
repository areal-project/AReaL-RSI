from enum import Enum


class ModelServiceInstanceStatus(str, Enum):
    FAILED = "FAILED"
    PENDING = "PENDING"
    READY = "READY"
    REGISTERED = "REGISTERED"
    STOPPED = "STOPPED"
    SUBMITTING = "SUBMITTING"
    UNHEALTHY = "UNHEALTHY"

    def __str__(self) -> str:
        return str(self.value)
