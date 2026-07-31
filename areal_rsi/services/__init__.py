"""Service interface contracts."""

from .areal import (
    AReaLService,
    AReaLServiceConfigurationError,
    AReaLSFTConfig,
    AReaLSFTConfigurationError,
    AReaLSFTSubmissionError,
    AReaLSFTTrainingError,
    AReaLSlurmConfig,
    AReaLSFTWrapper,
    MockAReaLService,
    MockAReaLRolloutConfig,
    MockAReaLRolloutConfigurationError,
    MockAReaLRolloutWrapper,
)
from .env_client import (
    EnvClient,
    EnvArenaClient,
    EnvArenaConfig,
    EnvArenaConfigurationError,
    EnvArenaError,
    EnvArenaHTTPError,
    EnvArenaVersionRef,
    MockEnvClient,
)
from .research import MockResearchAgent, OpenAIResearchAgent, ResearchAgent

__all__ = [
    "AReaLService",
    "AReaLServiceConfigurationError",
    "AReaLSFTConfig",
    "AReaLSFTConfigurationError",
    "AReaLSFTSubmissionError",
    "AReaLSFTTrainingError",
    "AReaLSlurmConfig",
    "AReaLSFTWrapper",
    "EnvClient",
    "EnvArenaConfig",
    "EnvArenaConfigurationError",
    "EnvArenaClient",
    "EnvArenaError",
    "EnvArenaHTTPError",
    "EnvArenaVersionRef",
    "MockAReaLService",
    "MockAReaLRolloutConfig",
    "MockAReaLRolloutConfigurationError",
    "MockAReaLRolloutWrapper",
    "MockEnvClient",
    "MockResearchAgent",
    "OpenAIResearchAgent",
    "ResearchAgent",
]
