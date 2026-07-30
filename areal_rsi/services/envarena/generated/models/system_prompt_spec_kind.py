from enum import Enum


class SystemPromptSpecKind(str, Enum):
    INLINE = "inline"
    OSS_OBJECT = "oss_object"

    def __str__(self) -> str:
        return str(self.value)
