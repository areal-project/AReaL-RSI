from enum import Enum


class RewardReviewEvidenceKind(str, Enum):
    COMMAND = "command"
    FILE = "file"
    HTTP = "http"
    OBSERVATION = "observation"
    SCREENSHOT = "screenshot"
    TRACE = "trace"

    def __str__(self) -> str:
        return str(self.value)
