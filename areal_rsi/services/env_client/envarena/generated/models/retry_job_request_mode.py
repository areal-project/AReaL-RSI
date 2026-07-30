from enum import Enum


class RetryJobRequestMode(str, Enum):
    ALL = "all"
    FAILED = "failed"
    SELECTED = "selected"

    def __str__(self) -> str:
        return str(self.value)
