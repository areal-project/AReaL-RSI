"""ResearchAgent contract and implementations."""

from .agent import ResearchAgent
from .claude_agent import ClaudeResearchAgent, ClaudeResearchConfig
from .mock import MockResearchAgent
from .openai_agent import OpenAIResearchAgent
from .sft_format import SFTFormatError, validate_sft_jsonl
from .trajectory_workspace import TrajectoryWorkspace

__all__ = [
    "ClaudeResearchAgent",
    "ClaudeResearchConfig",
    "MockResearchAgent",
    "OpenAIResearchAgent",
    "ResearchAgent",
    "SFTFormatError",
    "TrajectoryWorkspace",
    "validate_sft_jsonl",
]
