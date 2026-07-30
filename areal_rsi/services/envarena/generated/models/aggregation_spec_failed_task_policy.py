from enum import Enum


class AggregationSpecFailedTaskPolicy(str, Enum):
    EXCLUDE_FAILED = "exclude_failed"
    FAILED_AS_ZERO = "failed_as_zero"

    def __str__(self) -> str:
        return str(self.value)
