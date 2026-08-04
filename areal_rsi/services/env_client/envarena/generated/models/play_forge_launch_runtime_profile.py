from enum import Enum


class PlayForgeLaunchRuntimeProfile(str, Enum):
    PLAYFORGE = "playforge"

    def __str__(self) -> str:
        return str(self.value)
