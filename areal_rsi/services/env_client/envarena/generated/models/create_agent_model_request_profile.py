from enum import Enum


class CreateAgentModelRequestProfile(str, Enum):
    CLAUDE_CODE = "claude_code"
    DUAL = "dual"
    OPENAI = "openai"

    def __str__(self) -> str:
        return str(self.value)
