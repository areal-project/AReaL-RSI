from enum import Enum


class ModelServiceInstanceCheckOverallStatus(str, Enum):
    FAIL = "FAIL"
    PASS = "PASS"
    SKIPPED = "SKIPPED"
    WARN = "WARN"

    def __str__(self) -> str:
        return str(self.value)
