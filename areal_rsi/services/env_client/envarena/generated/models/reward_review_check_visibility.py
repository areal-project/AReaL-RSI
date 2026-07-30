from enum import Enum


class RewardReviewCheckVisibility(str, Enum):
    HIDDEN = "hidden"
    PUBLIC = "public"

    def __str__(self) -> str:
        return str(self.value)
