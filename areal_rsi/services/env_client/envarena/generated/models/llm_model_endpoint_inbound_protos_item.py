from enum import Enum


class LLMModelEndpointInboundProtosItem(str, Enum):
    CHAT = "chat"
    MESSAGES = "messages"
    RESPONSES = "responses"

    def __str__(self) -> str:
        return str(self.value)
