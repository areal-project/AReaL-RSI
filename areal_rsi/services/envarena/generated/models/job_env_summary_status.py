from enum import Enum


class JobEnvSummaryStatus(str, Enum):
    DONE = "DONE"
    FAILED = "FAILED"
    PARTIAL = "PARTIAL"
    PENDING = "PENDING"
    RUNNING = "RUNNING"

    def __str__(self) -> str:
        return str(self.value)
