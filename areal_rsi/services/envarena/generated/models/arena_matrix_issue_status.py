from enum import Enum


class ArenaMatrixIssueStatus(str, Enum):
    FIXED = "FIXED"
    WIP = "WIP"

    def __str__(self) -> str:
        return str(self.value)
