from enum import Enum


class RuntimeImageKind(str, Enum):
    CONTAINER_IMAGE = "container_image"

    def __str__(self) -> str:
        return str(self.value)
