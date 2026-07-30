from enum import Enum


class ScoreSchemaNormalization(str, Enum):
    ALREADY_0_1 = "already_0_1"
    CUSTOM = "custom"
    WEIGHTED_SUB_SCORES = "weighted_sub_scores"

    def __str__(self) -> str:
        return str(self.value)
