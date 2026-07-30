from enum import Enum


class SkillSourceKind(str, Enum):
    GIT = "git"
    LOCAL_PATH = "local_path"
    UPLOAD = "upload"
    URL = "url"

    def __str__(self) -> str:
        return str(self.value)
