from enum import Enum


class ModelServiceInstanceCheckObject(str, Enum):
    INSTANCE_CHECK = "instance_check"

    def __str__(self) -> str:
        return str(self.value)
