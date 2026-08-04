from enum import Enum


class PlayForgeGameAgentSkillSelectorSource(str, Enum):
    CATALOG = "catalog"
    USER = "user"

    def __str__(self) -> str:
        return str(self.value)
