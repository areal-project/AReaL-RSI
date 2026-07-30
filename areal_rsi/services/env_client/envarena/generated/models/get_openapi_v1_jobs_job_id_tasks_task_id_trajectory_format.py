from enum import Enum


class GetOpenapiV1JobsJobIdTasksTaskIdTrajectoryFormat(str, Enum):
    JSON = "json"
    RAW = "raw"

    def __str__(self) -> str:
        return str(self.value)
