from enum import Enum


class PlayForgeGameAgentSystemPromptSelectorSource(str, Enum):
    CATALOG = "catalog"
    USER = "user"

    def __str__(self) -> str:
        return str(self.value)
