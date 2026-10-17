"""Validate real grouped search rollouts before spending GPU time on updates."""

from __future__ import annotations

import argparse
import json
import os
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path

from areal_rsi.infra.artifacts import read_record
from examples.search.agent import Episode
from examples.search.pipeline import WorkerRequest
from examples.search.worker import dataset, prepare_config, workflow_kwargs


@dataclass(frozen=True, slots=True)
class PilotSample:
    episode: Episode
    audit: str
    output_hashes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PilotGroup:
    question_id: str
    rewards: tuple[float, ...]
    unique_trajectories: int
    search_calls: tuple[int, ...]
    audit_files: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PilotReport:
    questions: int
    samples_per_question: int
    mean_reward: float
    all_zero_fraction: float
    all_one_fraction: float
    mixed_group_fraction: float
    groups: tuple[PilotGroup, ...]


def run(request: WorkerRequest, questions: int) -> None:
    from areal.api.alloc_mode import ModelAllocation
    from areal.api.cli_args import SGLangConfig
    from areal.engine import RemoteSGLangEngine
    from areal.infra import LocalScheduler

    os.environ["QWEN_AGENT_MAX_LLM_CALL_PER_RUN"] = "50"
    os.environ["QWEN_AGENT_DEFAULT_MAX_INPUT_TOKENS"] = "115000"
    config = prepare_config(request)
    allocation = ModelAllocation.from_str(config.rollout.backend, name="rollout")
    scheduler = LocalScheduler(exp_config=config)
    rollout = RemoteSGLangEngine.as_controller(config.rollout, scheduler)
    items = list(dataset(request.dataset))[:questions]
    kwargs = workflow_kwargs(request)
    kwargs["evaluation_seed"] = None
    group_size = request.config.training.n_samples
    try:
        args = SGLangConfig.build_args(
            config.sglang, tp_size=allocation.parallel.tp_size, base_gpu_id=0
        )
        rollout.initialize(role="search-pilot", server_args=args)
        rollout.start_proxy()
        for item in items:
            rollout.submit(
                item,
                workflow="examples.search.agent.BrowseCompTrainAgent",
                workflow_kwargs=kwargs,
                group_size=group_size,
                is_eval=True,
                reward_normalization=False,
                drop_incomplete_group=False,
            )
        results = rollout.wait(len(items), timeout=None)
        if len(results) != len(items) or any(result is None for result in results):
            raise ValueError("Pilot did not return every complete group")
        episodes: dict[str, list[PilotSample]] = defaultdict(list)
        for path in (Path(request.output) / "episodes").glob("*.json"):
            if path.name.endswith(".audit.json"):
                continue
            episode = read_record(path, Episode)
            audit = json.loads(path.with_suffix(".audit.json").read_text())
            episodes[episode.question_id].append(
                PilotSample(
                    episode,
                    str(path.with_suffix(".audit.json")),
                    tuple(str(turn["output_sha256"]) for turn in audit["interactions"]),
                )
            )
        if set(episodes) != {item["question_id"] for item in items}:
            raise ValueError("Pilot question coverage is incomplete")
        groups = []
        for question_id, group in episodes.items():
            if len(group) != group_size or {item.episode.sample_index for item in group} != set(
                range(group_size)
            ):
                raise ValueError("Pilot sample slots are duplicated or incomplete")
            rewards = tuple(sample.episode.reward for sample in group)
            unique = len({sample.output_hashes for sample in group})
            groups.append(
                PilotGroup(
                    question_id,
                    rewards,
                    unique,
                    tuple(sample.episode.search_calls for sample in group),
                    tuple(sample.audit for sample in group),
                )
            )
        if not any(group.unique_trajectories > 1 for group in groups):
            raise ValueError("Independent sample slots produced no observed trajectory diversity")
        count = len(groups)
        report = PilotReport(
            count,
            group_size,
            sum(sum(group.rewards) for group in groups) / (count * group_size),
            sum(not any(group.rewards) for group in groups) / count,
            sum(all(group.rewards) for group in groups) / count,
            sum(0 < sum(group.rewards) < group_size for group in groups) / count,
            tuple(groups),
        )
        (Path(request.output) / "pilot.json").write_text(
            json.dumps(asdict(report), indent=2) + "\n"
        )
        print(
            json.dumps({key: value for key, value in asdict(report).items() if key != "groups"}),
            flush=True,
        )
    finally:
        rollout.destroy()
        scheduler.delete_workers()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", required=True, type=Path)
    parser.add_argument("--questions", default=8, type=int)
    args = parser.parse_args()
    if args.questions < 1:
        parser.error("--questions must be positive")
    run(read_record(args.request, WorkerRequest), args.questions)


if __name__ == "__main__":
    main()
