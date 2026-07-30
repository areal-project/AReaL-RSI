from enum import Enum


class GetOpenapiV1JobsJobIdTasksTaskIdLogsKindKind(str, Enum):
    HARNESS = "harness"
    REWARD = "reward"
    RUNNER = "runner"

    def __str__(self) -> str:
        return str(self.value)
