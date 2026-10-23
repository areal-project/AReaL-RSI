"""Run all 156 official references and two negative controls, without a model API."""

from __future__ import annotations

import argparse
import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from pathlib import Path

from evaluate import Result, Runtime, Status, rtl_evaluate

CONTROL_TASK = "Prob009_popcount3"


@dataclass(frozen=True)
class GoldenReport:
    expected_tasks: int
    task_count: int
    counts: dict[str, int]
    smoke: Result
    results: list[Result]
    invalid_verilog: Result
    wrong_output: Result
    accepted: bool


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--runtime", type=Runtime, choices=list(Runtime), default=Runtime.NATIVE)
    parser.add_argument("--image", help="Required for Docker or Apptainer")
    parser.add_argument("--jobs", type=int, default=4)
    args = parser.parse_args()
    if args.jobs < 1 or (args.runtime != Runtime.NATIVE and not args.image):
        parser.error("Positive --jobs and a container --image are required")
    dataset = args.dataset.resolve()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    tasks = sorted(dataset.glob("*_ref.sv"))
    for name in ["smoke", "golden", "negative-compile", "negative-output"]:
        (output / name).mkdir()

    def run(
        task: Path, group: str = "golden", invalid: bool = False, candidate: Path | None = None
    ) -> Result:
        try:
            return rtl_evaluate(
                "verilogeval",
                task,
                output / group,
                30,
                invalid,
                False,
                candidate=candidate,
                image=args.image,
                runtime=args.runtime,
            )
        except Exception as exc:
            return Result(task.name.removesuffix("_ref.sv"), Status.ERROR, str(exc), [])

    control = dataset / (CONTROL_TASK + "_ref.sv")
    smoke = run(control, "smoke")
    print(f"Smoke: {smoke.status.value}", flush=True)
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        results = []
        for result in pool.map(run, tasks):
            results.append(result)
            print(f"{result.task}: {result.status.value}", flush=True)
            # Persist each completed task before continuing, including failures.
            with (output / "results.jsonl").open("a") as stream:
                stream.write(json.dumps(asdict(result)) + "\n")
    invalid = run(control, "negative-compile", invalid=True)
    candidate = output / "wrong-popcount.sv"
    candidate.write_text(
        "module TopModule(input [2:0] in, output [1:0] out);\nassign out = 2'b00;\nendmodule\n"
    )
    wrong = run(control, "negative-output", candidate=candidate)
    accepted = (
        len(results) == 156
        and smoke.status == Status.PASS
        and all(result.status == Status.PASS for result in results)
        and invalid.status == Status.COMPILE_ERROR
        and wrong.status == Status.FAIL
    )
    report = GoldenReport(
        156,
        len(results),
        {status.value: sum(r.status == status for r in results) for status in Status},
        smoke,
        results,
        invalid,
        wrong,
        accepted,
    )
    (output / "summary.json").write_text(json.dumps(asdict(report), indent=2) + "\n")
    print(
        json.dumps(
            {
                "counts": report.counts,
                "accepted": accepted,
                "invalid_verilog": invalid.status.value,
                "wrong_output": wrong.status.value,
            }
        )
    )
    raise SystemExit(0 if accepted else 1)


if __name__ == "__main__":
    main()
