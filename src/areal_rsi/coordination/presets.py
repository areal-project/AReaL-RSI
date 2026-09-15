"""Runnable workflow simulations; real operators provide execution configuration."""

from __future__ import annotations

from pathlib import Path

from areal_rsi.api.research import ResearchLimits, TaskBrief, VerificationContract
from areal_rsi.coordination.config import ResearchConfig
from areal_rsi.execution import ApprovedCommand


def example_config(name: str, *, repository: Path) -> ResearchConfig:
    if name == "kernelbench":
        brief = TaskBrief(
            "为固定目标模型优化通用的 KernelBench harness",
            "只优化初始 DeepSeek harness 中的 skills/*.md。"
            "技能应适用于不同 kernel 任务，不能嵌入题目答案。"
            "检查开发轨迹，在 search 验证上评测初始 harness，安排一条研究方向，"
            "通过 verifier 调用固定 Task Agent 比较候选 harness。"
            "真实执行时另行提供目标模型和 GPU 配置。"
            "本次流程演练明确模拟 GPU 和 Task Agent 执行；请完成有意义的任务分配、文件修改和报告，"
            "并至少验证一次 RSI 明确回复后继续研究的过程。最终确认和文件导出只证明流程连通，"
            "不能据此声称 KernelBench 性能提升。",
            ResearchLimits(
                max_research_agents=1,
                max_research_tasks=6,
                max_model_calls=100,
                max_total_tokens=2_000_000,
                max_verify_calls=6,
                allowed_paths=("skills/*",),
            ),
            VerificationContract(
                "fast_1",
                target=0.65,
                description="固定目标模型和预算下，结果正确且运行更快的题目比例",
                identity={
                    "example": "kernelbench",
                    "feedback": "aggregate_only",
                    "require_final_confirmation": True,
                },
            ),
        )
        seed = repository / "examples" / "kernelbench" / "seed"
    else:
        raise ValueError("Unknown example template")
    return ResearchConfig(
        brief=brief,
        seed_dir=str(seed),
        verifier=ApprovedCommand(
            "verify", ("simulated-verification", "{workspace}", "{output}"), ("workspace", "output")
        ),
        metadata={
            "template": name,
            "simulation_only": True,
            "evaluate_seed_as_baseline": name == "kernelbench",
        },
    )
