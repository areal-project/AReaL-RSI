from enum import Enum


class BenchmarkJobKind(str, Enum):
    BENCHMARK = "BENCHMARK"
    RESCORE = "RESCORE"

    def __str__(self) -> str:
        return str(self.value)
