"""Runnable workflow simulations; real operators provide execution configuration."""

from __future__ import annotations

from pathlib import Path

from areal_rsi.api.research import ResearchLimits, TaskBrief, VerificationContract
from areal_rsi.coordination.config import ResearchConfig
from areal_rsi.execution import ApprovedCommand


def example_config(name: str, *, repository: Path) -> ResearchConfig:
    if name == "kernelbench":
        brief = TaskBrief(
            "Improve a reusable KernelBench harness for a fixed task model",
            "Optimize only skills/*.md in the initial DeepSeek harness. Keep the skill general "
            "across kernel tasks; never embed task answers. Inspect development trajectories, "
            "evaluate the initial harness on search validation, assign one research direction, "
            "and use the fixed Task Agent through the verifier to compare candidate harnesses. "
            "The target model and GPU configuration will be supplied for real execution. "
            "For this workflow rehearsal GPU and Task Agent execution are explicitly simulated; "
            "exercise meaningful assignments, file edits, reports and at least one explicit RSI "
            "reply. Final confirmation and exported files demonstrate the workflow, not actual "
            "KernelBench improvement.",
            ResearchLimits(
                max_agents=1,
                max_nodes=6,
                max_model_calls=100,
                max_total_tokens=2_000_000,
                max_verify_calls=6,
                allowed_paths=("skills/*",),
            ),
            VerificationContract(
                "fast_1",
                target=0.65,
                description="Correct and faster fraction under a fixed task model and budget",
                identity={
                    "example": "kernelbench",
                    "feedback": "aggregate_only",
                    "require_final_confirmation": True,
                },
            ),
        )
        seed = repository / "examples" / "kernelbench" / "seed"
    elif name == "cuhkx":
        brief = TaskBrief(
            "Research and improve CUHK-X small-model training and design",
            "Optimize architecture, training and allowed multimodal design for CUHK-X's 40-class "
            "human activity recognition task. No RGB camera input, large pretrained backbone, "
            "or model over 100 MB. The user has confirmed organizer authorization for this "
            "LLM-assisted workflow. Search actual papers and mainstream methods to inform your "
            "plan; decide useful research directions yourself, within the agent limit. Reuse the "
            "provided small CNN as reference material; there is no evaluated starting baseline. "
            "Keep validation subjects separate and prediction paths anonymous. For this rehearsal "
            "training/GPU and validation scores are explicitly simulated. Demonstrate paper "
            "research, multiple directions if justified, evidence sharing, an explicit next "
            "assignment, and final verification. Do not claim competition performance from it.",
            ResearchLimits(
                max_agents=2,
                max_nodes=8,
                max_model_calls=150,
                max_total_tokens=3_000_000,
                max_verify_calls=6,
            ),
            VerificationContract(
                "accuracy",
                target=0.65,
                description="Clip accuracy on a frozen subject-separated anonymous validation set",
                identity={
                    "example": "cuhkx",
                    "classes": 40,
                    "max_model_bytes": 100_000_000,
                    "require_final_confirmation": True,
                },
            ),
        )
        seed = repository / "examples" / "cuhkx" / "seed"
    else:
        raise ValueError("Unknown example template")
    if name == "cuhkx":
        brief.instructions += (
            " This is a bounded workflow rehearsal: inspect enough primary-source evidence "
            "to choose feasible directions, then act on the findings; an exhaustive literature "
            "survey is unnecessary. Simulation jobs publish simulation.json, not weights. "
            "The simulated verifier accepts a frozen source-only candidate and does not "
            "require an actual checkpoint. Exercise candidate refinement and report decisions "
            "without waiting for real training, credentials or GPU resources. Keep reports "
            "and shared evidence summaries concise; archive detailed literature separately."
        )
    return ResearchConfig(
        brief=brief,
        seed_dir=str(seed),
        commands=(
            ApprovedCommand(
                "train", ("simulated-training", "{workspace}", "{output}"), ("workspace", "output")
            ),
        )
        if name == "cuhkx"
        else (),
        verifier=ApprovedCommand(
            "verify", ("simulated-verification", "{workspace}", "{output}"), ("workspace", "output")
        ),
        metadata={
            "template": name,
            "simulation_only": True,
            "evaluate_seed_as_baseline": name == "kernelbench",
        },
    )
