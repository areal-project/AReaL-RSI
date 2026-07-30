from enum import Enum


class RewardReviewSchema(str, Enum):
    X2ENV_REWARD_REVIEW_V1 = "x2env.reward-review.v1"

    def __str__(self) -> str:
        return str(self.value)
