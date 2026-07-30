from enum import Enum


class AggregationSpecMethod(str, Enum):
    MEAN = "mean"
    PASS_AT_K = "pass_at_k"
    WEIGHTED_MEAN = "weighted_mean"

    def __str__(self) -> str:
        return str(self.value)
