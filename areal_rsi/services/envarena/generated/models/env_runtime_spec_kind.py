from enum import Enum


class EnvRuntimeSpecKind(str, Enum):
    FILES = "files"
    IMAGE = "image"

    def __str__(self) -> str:
        return str(self.value)
