from enum import Enum


class ModelServiceGatewayConfigInboundProtosItem(str, Enum):
    CHAT = "chat"
    MESSAGES = "messages"
    RESPONSES = "responses"

    def __str__(self) -> str:
        return str(self.value)
