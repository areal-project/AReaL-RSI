from enum import Enum


class LLMEndpointHealthState(str, Enum):
    CHECKING = "checking"
    DISABLED = "disabled"
    HEALTHY = "healthy"
    UNHEALTHY = "unhealthy"
    UNMONITORED = "unmonitored"

    def __str__(self) -> str:
        return str(self.value)
