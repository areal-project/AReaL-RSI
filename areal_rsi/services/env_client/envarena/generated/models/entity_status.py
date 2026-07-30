from enum import Enum


class EntityStatus(str, Enum):
    ARCHIVED = "ARCHIVED"
    DEPRECATED = "DEPRECATED"
    DRAFT = "DRAFT"
    PUBLISHED = "PUBLISHED"

    def __str__(self) -> str:
        return str(self.value)
