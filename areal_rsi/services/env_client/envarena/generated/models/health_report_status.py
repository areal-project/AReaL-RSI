from enum import Enum


class HealthReportStatus(str, Enum):
    DEGRADED = "degraded"
    OK = "ok"

    def __str__(self) -> str:
        return str(self.value)
