from enum import Enum


class DependencyCheckStatus(str, Enum):
    DEGRADED = "degraded"
    DISABLED = "disabled"
    OK = "ok"

    def __str__(self) -> str:
        return str(self.value)
