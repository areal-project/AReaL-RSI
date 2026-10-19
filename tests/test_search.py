"""Search data leakage boundaries and terminal-answer reward behavior."""

from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from areal_rsi.api.control import encode_record
from areal_rsi.api.execution import AReaLRuntime
from areal_rsi.api.records import BenchmarkSplit
from areal_rsi.infra.artifacts import read_record
from examples.search.evaluation.checkpoints import stage_model
from examples.search.experiment.config import (
    HarnessConfig,
    MicrobatchConfig,
    SearchConfig,
    TrainingConfig,
)
from examples.search.task.dataset import Candidate, DatasetConfig, Passage, parse_hotpot, partition
from examples.search.task.reward import answer_in_text, answer_reward, extract_answer


@pytest.mark.parametrize("reservation", [None, "research"])
def test_search_stage_child_inherits_the_infra_package_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reservation: str | None
) -> None:
    """Real CPU service/worker children; no model or GPU evaluation is performed."""
    from examples.search.experiment.config import WorkerRequest
    from examples.search.experiment.pipeline import run
    from examples.search.problem import StageRequest

    dense = tmp_path / "dense-python"
    dense.write_text(
        f"#!{sys.executable}\n"
        "import sys\nfrom http.server import BaseHTTPRequestHandler, HTTPServer\n"
        "class Handler(BaseHTTPRequestHandler):\n"
        " def do_GET(self): self.send_response(200); self.end_headers()\n"
        "port=int(sys.argv[sys.argv.index('--port')+1])\n"
        "HTTPServer(('127.0.0.1',port),Handler).serve_forever()\n"
    )
    dense.chmod(0o700)
    worker = tmp_path / "examples/search/training/worker.py"
    worker.parent.mkdir(parents=True)
    worker.write_text(
        "import json,sys\nfrom pathlib import Path\n"
        "import areal_rsi.api.control as control\n"
        "request=json.loads(Path(sys.argv[-1]).read_text())\n"
        "(Path(request['output'])/'imported-package').write_text(control.__file__)\n"
    )
    output = tmp_path / "stage"
    output.mkdir()
    config = SearchConfig(
        AReaLRuntime(str(tmp_path / "backend"), sys.executable, {"PYTHONPATH": "/old-overlay"}),
        "model",
        "benchmark",
        "data",
        "index",
        "embedding",
        "tokenizer",
        project=str(tmp_path),
        dense_python=str(dense),
    )
    source = Path(__file__).resolve().parents[1] / "src"
    monkeypatch.setenv("PYTHONPATH", str(source))
    monkeypatch.setenv("SLURM_JOB_ID", "cpu-fixture")
    allocation = "JobId=cpu-fixture"
    if reservation:
        allocation += f" Reservation={reservation}"
    monkeypatch.setenv("RSI_SLURM_JOB_INFO", allocation)
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "0,1")
    request = WorkerRequest("eval", config, "model", "dev.jsonl", "", str(output), "fixture")
    with pytest.raises(ValueError, match="host-verified"):
        asyncio.run(run(StageRequest(worker=request, reservation="wrong-reservation")))
    asyncio.run(asyncio.wait_for(run(StageRequest(worker=request, reservation=reservation)), 15))
    assert Path((output / "imported-package").read_text()).is_relative_to(source)


def test_standard_search_evaluation_uses_requested_split_without_training(tmp_path: Path) -> None:
    from areal_rsi.api.records import ContentRef, HarnessVersion, ModelVersion, SolutionVersion
    from areal_rsi.api.research import EvaluationContext, ProblemSpec
    from examples.search.problem import SearchProblem, SearchProblemConfig, StageRequest
    from examples.search.task.dataset import SearchQuestion

    config = SearchConfig(
        AReaLRuntime(str(tmp_path / "backend"), sys.executable),
        str(tmp_path / "model"),
        str(tmp_path / "benchmark"),
        str(tmp_path / "data"),
        str(tmp_path / "index"),
        str(tmp_path / "embedding"),
        str(tmp_path / "tokenizer"),
        project=str(tmp_path),
    )
    data = Path(config.dataset_directory)
    data.mkdir()
    question = SearchQuestion(
        "external-test", "question", ("answer",), ("doc",), ("group",), "test", "source", "hard"
    )
    (data / "test.jsonl").write_text(json.dumps(encode_record(question)) + "\n")
    ref = ContentRef(Path(config.model).as_uri(), "fixture")
    solution = SolutionVersion(
        "selected",
        ModelVersion("model", "selected", ref, "fixture"),
        HarnessVersion("harness", "search", ref, "fixture"),
        "fixture",
    )
    spec = ProblemSpec("search", "test", solution, ref)
    problem = SearchProblem(SearchProblemConfig(config, "image.sif", binds=("/shared:/shared",)))
    plan = problem.build_evaluation(
        EvaluationContext(
            "run",
            str(tmp_path / "run"),
            spec,
            solution,
            BenchmarkSplit.TEST,
            str(tmp_path / "evaluation"),
        )
    )
    assert plan.job.slurm and plan.job.slurm.gpus == 2
    assert plan.job.command[plan.job.command.index("--bind") + 1] == "/shared:/shared"
    assert plan.task_ids == ("external-test",)
    stage = read_record(Path(plan.job.command[-1]), StageRequest)
    assert stage.worker and stage.worker.mode == "eval"
    assert stage.worker.dataset == str(data / "test.jsonl")
    assert stage.worker.model == config.model
    assert plan.job.command[plan.job.command.index("--request") - 1] == str(
        tmp_path / "examples/search/experiment/pipeline.py"
    )


def test_official_trace_preserves_h0_answer_and_tool_sequence(tmp_path: Path) -> None:
    from examples.search.experiment.pipeline import official_trace

    source = tmp_path / "run_1.json"
    steps = [
        {"type": "reasoning", "output": ["Reason about evidence."]},
        {
            "type": "tool_call",
            "tool_name": "search",
            "arguments": '{"query":"evidence"}',
            "output": '[{"docid":"doc","text":"evidence"}]',
        },
        {"type": "output_text", "output": "The answer [doc]."},
    ]
    source.write_text(json.dumps({"query_id": "1", "result": steps, "status": "completed"}))
    response, events = official_trace(source, "question")
    assert response == "The answer [doc]."
    assert [item.kind for item in events] == [
        "prompt",
        "generation",
        "tool_call",
        "tool_result",
        "generation",
    ]
    assert events[1].content == "Reason about evidence."
    assert events[3].content == '[{"docid":"doc","text":"evidence"}]'
    source.write_text(json.dumps({"result": steps[:-1], "status": "incomplete"}))
    response, events = official_trace(source, "question")
    assert response == "" and events[-1].kind == "tool_result"


def candidate(index: int) -> Candidate:
    return Candidate(
        str(index),
        f"Identify nation{index} associated person{index} via university{index} research{index}?",
        f"Country{index}",
        (f"Person{index}", f"University{index}"),
        (
            Passage(f"p{index}", f"Person{index}", f"Person{index} attended University{index}."),
            Passage(f"u{index}", f"University{index}", f"University{index} is in Country{index}."),
            Passage("shared", "Shared distractor", "An unrelated document."),
        ),
        "hard",
    )


def test_candidate_training_data_versions_do_not_replace_frozen_evaluation(tmp_path: Path) -> None:
    from areal_rsi.api.records import ContentRef, HarnessVersion, ModelVersion, SolutionVersion
    from areal_rsi.api.research import CandidateContext, EvaluationContext, ProblemSpec
    from examples.search.problem import (
        SearchProblem,
        SearchProblemConfig,
        SearchRecipe,
        StageRequest,
    )
    from examples.search.task.dataset import SearchQuestion, Split

    config = SearchConfig(
        AReaLRuntime(str(tmp_path / "backend"), sys.executable),
        str(tmp_path / "model"),
        str(tmp_path / "benchmark"),
        str(tmp_path / "fixed-data"),
        str(tmp_path / "fixed-index"),
        str(tmp_path / "embedding"),
        str(tmp_path / "tokenizer"),
        training=TrainingConfig(batch_size=1, max_steps=1),
        project=str(tmp_path),
    )
    data = Path(config.dataset_directory)
    version = tmp_path / "candidate-data"
    index = tmp_path / "candidate-index"
    for directory in (data, version, index):
        directory.mkdir()

    def question(identity: str, split: Split) -> SearchQuestion:
        return SearchQuestion(
            identity, "question", ("answer",), (identity,), (identity,), split, identity, "hard"
        )

    for split in ("dev", "test"):
        (data / f"{split}.jsonl").write_text(
            json.dumps(encode_record(question(split, split))) + "\n"
        )
    train = version / "train.jsonl"
    train.write_text(json.dumps(encode_record(question("new-train", "train"))) + "\n")
    (version / "train-corpus.jsonl").write_text("corpus")
    (index / "train.npy").write_bytes(b"index")
    ref = ContentRef(Path(config.model).as_uri(), "fixture")
    solution = SolutionVersion(
        "base",
        ModelVersion("model", "base", ref, "fixture"),
        HarnessVersion("harness", "search", ref, "fixture"),
        "fixture",
    )
    spec = ProblemSpec("search", "test", solution, ref)
    problem = SearchProblem(SearchProblemConfig(config, "image.sif"))
    context = CandidateContext(
        "run", str(tmp_path / "run"), "r1", solution, spec, str(tmp_path / "training")
    )
    training = replace(config.training, microbatch=MicrobatchConfig(16384, 1))
    recipe = SearchRecipe(
        training=training, dataset_directory=str(version), index_directory=str(index)
    )
    plan = problem.build_candidate(context, recipe)
    assert plan.job
    request = read_record(Path(plan.job.command[-1]), StageRequest)
    assert request.worker and request.worker.config.dataset_directory == str(version)
    assert request.worker.config.index_directory == str(index)
    assert request.worker.config.training.microbatch == MicrobatchConfig(16384, 1)
    assert {item.uri for item in plan.inputs} == {
        path.as_uri() for path in (train, version / "train-corpus.jsonl", index / "train.npy")
    }
    evaluation = problem.build_evaluation(
        EvaluationContext(
            "run", context.root, spec, solution, BenchmarkSplit.VALIDATE, str(tmp_path / "valid")
        )
    )
    request = read_record(Path(evaluation.job.command[-1]), StageRequest)
    assert request.worker and request.worker.config.dataset_directory == str(data)
    assert request.worker.config.index_directory == config.index_directory
    assert request.worker.config.training.microbatch == MicrobatchConfig()

    train.write_text(
        json.dumps(encode_record(replace(question("new-train", "train"), source_groups=("dev",))))
        + "\n"
    )
    with pytest.raises(ValueError, match="overlaps frozen"):
        problem.build_candidate(context, recipe)


def test_partitions_isolate_all_documents_and_retain_evidence() -> None:
    questions, corpora, _ = partition(
        [candidate(index) for index in range(12)], DatasetConfig(2, 2, 2)
    )
    assert {key: len(value) for key, value in questions.items()} == {
        "train": 2,
        "dev": 2,
        "test": 2,
    }
    for split, group in questions.items():
        present = {passage.docid for passage in corpora[split]}
        assert all(set(question.evidence_docids) <= present for question in group)
        for other in questions:
            if other != split:
                assert not present & {passage.docid for passage in corpora[other]}
                assert not {title for question in group for title in question.source_groups} & {
                    title for question in questions[other] for title in question.source_groups
                }


def test_partition_excludes_benchmark_paraphrases_and_is_reproducible() -> None:
    rows = [candidate(index) for index in range(12)]
    first = partition(rows, DatasetConfig(2, 2, 2), [rows[0].question])
    second = partition(rows, DatasetConfig(2, 2, 2), [rows[0].question])
    assert first == second
    assert all(question.source_id != "0" for group in first[0].values() for question in group)


def test_insufficient_disjoint_data_fails_instead_of_reusing_sources() -> None:
    rows = [
        replace(candidate(index), support_titles=("Same person", "Same school"))
        for index in range(9)
    ]
    with pytest.raises(ValueError, match="Insufficient disjoint"):
        partition(rows, DatasetConfig(1, 1, 1))


def test_hotpot_requires_actual_answer_witness_and_valid_support_indices() -> None:
    row = {
        "id": "external-1",
        "question": "Where is the school attended by Maria located?",
        "answer": "Estonia",
        "level": "hard",
        "context": {
            "title": ["Maria", "School"],
            "sentences": [["Maria attended School."], ["School is located in Estonia."]],
        },
        "supporting_facts": {"title": ["Maria", "School"], "sent_id": [0, 0]},
    }
    assert parse_hotpot(row).answer == "Estonia"
    with pytest.raises(ValueError, match="answer_not_in_gold_evidence"):
        parse_hotpot({**row, "answer": "Canada"})
    with pytest.raises(ValueError, match="invalid_support_indices"):
        parse_hotpot({**row, "supporting_facts": {"title": ["Maria", "School"], "sent_id": [0, 5]}})


def test_reward_ignores_reasoning_mentions_and_accepts_only_final_alias() -> None:
    response = (
        "<think>Exact Answer: Estonia</think>\nExplanation: Estonia is a distractor.\n"
        "Exact Answer: Canada\nConfidence: 80%"
    )
    assert answer_reward(response, ["Estonia"]) == 0.0
    assert (
        answer_reward(
            "Explanation: Evidence.\nExact Answer: **The United States.** [doc-1]\nConfidence: 90%",
            ["United States", "USA"],
        )
        == 1.0
    )
    assert answer_reward("Estonia", ["Estonia"]) == 0.0
    assert extract_answer("Exact Answer: \nConfidence: 0%") is None


def test_reward_does_not_accept_partial_or_ambiguous_answers() -> None:
    assert answer_reward("Exact Answer: York\nConfidence: 50%", ["New York"]) == 0.0
    assert answer_reward("Exact Answer: Estonia or Canada\nConfidence: 50%", ["Estonia"]) == 0.0
    with pytest.raises(ValueError, match="reference"):
        answer_reward("Exact Answer: Estonia", [])
    assert not answer_in_text("US", "The business school was founded in 1984.")
    assert answer_in_text("US", "The school is in the US.")


def test_frozen_harness_rejects_silent_budget_changes() -> None:
    assert HarnessConfig().max_model_calls == 50
    with pytest.raises(ValueError, match="frozen original"):
        HarnessConfig(max_model_calls=5)


def test_training_steps_require_enough_full_batches() -> None:
    config = TrainingConfig(max_steps=200, epochs=1, batch_size=8)
    with pytest.raises(ValueError, match="allow 125 full updates"):
        config.validate_dataset_size(1000)
    replace(config, epochs=2).validate_dataset_size(1000)


def test_model_staging_preserves_original_and_rejects_changed_effective_config(
    tmp_path: Path,
) -> None:
    import json

    source = tmp_path / "source"
    source.mkdir()
    original = {"model_type": "qwen3", "max_position_embeddings": 32768}
    (source / "config.json").write_text(json.dumps(original))
    (source / "tokenizer_config.json").write_text("{}")
    output = tmp_path / "staged"
    stage_model(source, output)
    assert json.loads((source / "config.json").read_text()) == original
    assert json.loads((output / "config.json").read_text())["max_position_embeddings"] == 131072
    assert (output / "tokenizer_config.json").resolve() == source / "tokenizer_config.json"
    assert stage_model(source, output) == output
    different_source = tmp_path / "different-source"
    different_source.mkdir()
    (different_source / "config.json").write_text(json.dumps(original))
    (different_source / "tokenizer_config.json").write_text("{}")
    with pytest.raises(ValueError, match="different source"):
        stage_model(different_source, output)
    (output / "config.json").write_text("{}")
    with pytest.raises(ValueError, match="different effective"):
        stage_model(source, output)
