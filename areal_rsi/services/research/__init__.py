"""ResearchAgent contract and implementations."""

from .agent import ResearchAgent
from .claude_agent import ClaudeResearchAgent, ClaudeResearchConfig
from .experience_skill import build_experience_skill, experience_records_from_result
from .mock import MockResearchAgent
from .openai_agent import OpenAIResearchAgent
from .sft_format import SFTFormatError, validate_sft_jsonl
from .trajectory_workspace import TrajectoryWorkspace

__all__ = [
    "ClaudeResearchAgent",
    "ClaudeResearchConfig",
    "build_experience_skill",
    "experience_records_from_result",
    "MockResearchAgent",
    "OpenAIResearchAgent",
    "ResearchAgent",
    "SFTFormatError",
    "TrajectoryWorkspace",
    "validate_sft_jsonl",
]
