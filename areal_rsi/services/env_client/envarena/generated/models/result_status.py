from enum import Enum


class ResultStatus(str, Enum):
    CANCELLED = "CANCELLED"
    EVAL_FAILED = "EVAL_FAILED"
    FAILED = "FAILED"
    HARNESS_FAILED = "HARNESS_FAILED"
    OK = "OK"
    TIMEOUT = "TIMEOUT"

    def __str__(self) -> str:
        return str(self.value)
