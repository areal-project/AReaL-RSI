"""AReaL service contract and implementations."""

from .mock import MockAReaLService
from .service import AReaLService

__all__ = ["AReaLService", "MockAReaLService"]
