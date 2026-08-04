from enum import Enum


class ModelServiceCatalogParametersObject(str, Enum):
    MODEL_PARAMETERS = "model_parameters"

    def __str__(self) -> str:
        return str(self.value)
