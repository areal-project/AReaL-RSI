from enum import Enum


class HarnessRuntimeSpecType(str, Enum):
    CLAUDE_CODE = "claude_code"
    CODEX = "codex"

    def __str__(self) -> str:
        return str(self.value)
