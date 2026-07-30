from enum import Enum


class RewardReviewAuthoritative(str, Enum):
    SCRIPT = "script"

    def __str__(self) -> str:
        return str(self.value)
