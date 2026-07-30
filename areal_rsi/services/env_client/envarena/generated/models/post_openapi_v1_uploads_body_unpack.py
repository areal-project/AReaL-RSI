from enum import Enum


class PostOpenapiV1UploadsBodyUnpack(str, Enum):
    ZIP = "zip"

    def __str__(self) -> str:
        return str(self.value)
