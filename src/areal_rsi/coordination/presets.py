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
                description="固定目标模型和预算下，结果正确且运行更快的题目比例",
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
            "调研并优化 CUHK-X 小模型训练与设计",
            "针对 CUHK-X 的 40 类人体活动识别任务，优化架构、训练与允许的多模态设计。"
            "不得使用 RGB 摄像头输入、大型预训练骨干或超过 100 MB 的模型。"
            "用户已确认主办方允许本次 LLM 辅助工作流。搜索真实论文和主流方法支持规划，"
            "在 Agent 数量上限内自主决定有价值的方向。"
            "提供的小型 CNN 仅作为参考源码，没有已评测 baseline。"
            "验证 subject 与训练分开，推理路径匿名。本次演练明确模拟训练、GPU 和验证分数。"
            "请演练论文调研、必要时的多方向探索、共享证据、明确的下一项任务和最终验证，"
            "不能据此声称取得比赛成绩。",
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
                description="在固定、按 subject 隔离且匿名的验证集上计算 clip 准确率",
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
            "这是有边界的流程演练：查看足以选择可行方向的一手证据，然后执行，不需要穷尽文献。"
            "模拟作业只发布 simulation.json，不产生权重；模拟 verifier 接受冻结的纯源码候选，"
            "不要求实际 checkpoint。请演练候选改进与报告决策，不等待真实训练、凭据或 GPU 资源。"
            "报告和公共证据摘要保持简洁，详细文献另行归档。"
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
