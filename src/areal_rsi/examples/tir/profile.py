"""Task choices for the calculator/math RSI example."""

from areal_rsi.infra.coordination.evaluation.profile import PromptProfile

PROFILE = PromptProfile(
    config_class="examples.tir.tir_workflow.TIRGRPOConfig",
    workflow="areal_rsi.infra.runtime.tir.FrozenTIRWorkflow",
    reward_fn="areal_rsi.infra.coordination.evaluation.tir.verifier.strict_tir_reward_fn",
    verify_fn="areal_rsi.infra.coordination.evaluation.tir.verifier.strict_math_reward",
    runner_directory="examples/tir",
    boxed_answers=True,
    enabled_tools=("calculator",),
    researcher_instruction=(
        "Improve one reusable prompt for a math agent with a calculator tool. "
        "Return only a prompt template containing exactly one literal {prompt} field. "
        "Include 'User: {prompt}' verbatim. Do not repeat input JSON or specific questions, "
        "answers, task IDs or benchmark examples. Keep <calculator>expression</calculator> "
        "syntax and a boxed final answer. Escape other braces, e.g. \\boxed{{answer}}."
    ),
)
