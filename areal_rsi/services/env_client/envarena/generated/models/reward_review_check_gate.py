from enum import Enum


class RewardReviewCheckGate(str, Enum):
    P0 = "p0"
    SCORED = "scored"

    def __str__(self) -> str:
        return str(self.value)
