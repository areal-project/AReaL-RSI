from enum import Enum


class ToolManifestKind(str, Enum):
    COMMAND = "command"
    MCP = "mcp"
    SETTINGS = "settings"

    def __str__(self) -> str:
        return str(self.value)
