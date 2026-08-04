from enum import Enum


class ModelServiceCatalogModelAvailabilityStatus(str, Enum):
    AVAILABLE = "AVAILABLE"
    PATH_MISSING = "PATH_MISSING"
    PATH_UNREADABLE = "PATH_UNREADABLE"
    WEIGHTS_MISSING = "WEIGHTS_MISSING"
    WEIGHTS_UNREADABLE = "WEIGHTS_UNREADABLE"

    def __str__(self) -> str:
        return str(self.value)
