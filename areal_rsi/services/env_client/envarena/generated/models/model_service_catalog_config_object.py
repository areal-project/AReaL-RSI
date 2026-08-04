from enum import Enum


class ModelServiceCatalogConfigObject(str, Enum):
    MODEL_CONFIG = "model_config"

    def __str__(self) -> str:
        return str(self.value)
