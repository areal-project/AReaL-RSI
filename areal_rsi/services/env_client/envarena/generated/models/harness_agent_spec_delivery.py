from enum import Enum


class HarnessAgentSpecDelivery(str, Enum):
    BUILTIN = "builtin"

    def __str__(self) -> str:
        return str(self.value)
