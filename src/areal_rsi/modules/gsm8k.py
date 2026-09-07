"""GSM8K 示例：固定上游数据，从官方 train 留出研究验证集，不消费官方 test。"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import tempfile
import urllib.request
from collections.abc import Sequence
from decimal import Decimal
from pathlib import Path
from typing import Any

from areal_rsi.api.base import (
    BenchmarkSplit,
    Task,
    VerificationRequest,
    VerificationResult,
)
from areal_rsi.base.storage import FileContentStore, decode, json_bytes, write_json

REVISION = "3101c7d5072418e28b9008a6636bde82a006892c"
TRAIN_SHA256 = "17f347dc51477c50d4efb83959dbb7c56297aba886e5544ee2aaed3024813465"
SOURCE = f"https://raw.githubusercontent.com/openai/grade-school-math/{REVISION}"
ANSWER = re.compile(r"####\s*([+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)\s*")


def final_answer(text: str) -> Decimal | None:
    """只接受最后一行的完整数字，避免把 12abc、1e9 等前缀误判为答案。"""
    lines = text.strip().splitlines()
    match = ANSWER.fullmatch(lines[-1]) if lines else None
    return Decimal(match[1].replace(",", "")) if match else None


def prepare_dataset(raw: bytes, destination: Path, *, source: str) -> None:
    """公开文件仅含题面；答案和解题过程保存在不提供给 Agent 的 private/ 下。"""
    rows = [json.loads(line) for line in raw.splitlines() if line.strip()]
    if len(rows) < 10:
        raise ValueError("数据过少，至少需要 10 道题才能划分 TRAIN/VALIDATE")
    questions: set[str] = set()
    for row in rows:
        question = row.get("question")
        answer = row.get("answer")
        if not isinstance(question, str) or not question.strip() or question in questions:
            raise ValueError("题面为空或重复")
        if not isinstance(answer, str) or final_answer(answer) is None:
            raise ValueError("数据答案不符合 #### NUMBER 契约")
        questions.add(question)
    # 稳定哈希排序后每十题留一题验证。固定划分后才限量，改变 limit 不会跨 split。
    ordered = sorted(
        enumerate(rows), key=lambda pair: hashlib.sha256(pair[1]["question"].encode()).digest()
    )
    public: dict[str, list[Any]] = {split.value: [] for split in BenchmarkSplit}
    expected = {}
    for position, (original_index, row) in enumerate(ordered):
        split = BenchmarkSplit.VALIDATE if position % 10 == 0 else BenchmarkSplit.TRAIN
        task_id = f"gsm8k_train_{original_index}"
        public[split.value].append(Task(task_id, {"question": row["question"]}))
        expected[task_id] = row["answer"]
    if destination.exists():
        raise FileExistsError("目标目录已存在；数据集不可就地覆盖，请使用新目录")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=destination.parent) as temporary:
        staged = Path(temporary) / "dataset"
        staged.mkdir()
        blobs = {
            "train.json": json_bytes(public["train"]),
            "validate.json": json_bytes(public["validate"]),
            "private/answers.json": json_bytes(expected),
        }
        for name, data in blobs.items():
            path = staged / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        write_json(
            staged / "manifest.json",
            {
                "schema": "gsm8k-rsi-v1",
                "source": source,
                "source_sha256": hashlib.sha256(raw).hexdigest(),
                "split_rule": "question-sha256-order-every-tenth-validate-v1",
                "files": {name: hashlib.sha256(data).hexdigest() for name, data in blobs.items()},
                "counts": {name: len(tasks) for name, tasks in public.items()},
            },
        )
        staged.rename(destination)


def read_checked(directory: Path, manifest: dict[str, Any], name: str) -> Any:
    data = (directory / name).read_bytes()
    if hashlib.sha256(data).hexdigest() != manifest["files"][name]:
        raise ValueError(f"GSM8K 数据摘要不一致：{name}")
    return json.loads(data)


class Gsm8kBenchmark:
    def __init__(self, directory: Path, *, train_limit: int = 16, validate_limit: int = 16):
        if train_limit < 1 or validate_limit < 1:
            raise ValueError("评测题数必须为正数")
        self.directory = directory.resolve()
        self.manifest = json.loads((self.directory / "manifest.json").read_text())
        if self.manifest["schema"] != "gsm8k-rsi-v1":
            raise ValueError("不支持的数据集格式")
        self.tasks: dict[BenchmarkSplit, tuple[Task, ...]] = {}
        for split, limit in (
            (BenchmarkSplit.TRAIN, train_limit),
            (BenchmarkSplit.VALIDATE, validate_limit),
        ):
            rows = read_checked(self.directory, self.manifest, f"{split.value}.json")
            if limit > len(rows):
                raise ValueError(f"{split.value} 只有 {len(rows)} 题，不能请求 {limit} 题")
            self.tasks[split] = tuple(decode(Task, row) for row in rows[:limit])
        ids = [task.task_id for tasks in self.tasks.values() for task in tasks]
        if len(set(ids)) != len(ids):
            raise ValueError("TRAIN/VALIDATE 存在重复任务")
        identity = hashlib.sha256(json_bytes([self.manifest, ids])).hexdigest()[:16]
        self.name = f"gsm8k-{identity}"

    async def list_tasks(self, split: BenchmarkSplit) -> Sequence[Task]:
        # API 对象可变，返回独立拷贝，调用方不能修改后续评测题面。
        tasks: tuple[Task, ...] = decode(
            tuple[Task, ...], json.loads(json_bytes(self.tasks[split]))
        )
        return tasks


class Gsm8kVerifier:
    def __init__(self, content: FileContentStore, benchmark: Gsm8kBenchmark):
        self.content = content
        self.expected = read_checked(
            benchmark.directory, benchmark.manifest, "private/answers.json"
        )
        self.inputs = {
            task.task_id: json_bytes(task.input)
            for tasks in benchmark.tasks.values()
            for task in tasks
        }

    async def verify(self, request: VerificationRequest) -> VerificationResult:
        task, solution, result = request.task, request.solution, request.result
        if self.inputs.get(task.task_id) != json_bytes(task.input):
            raise ValueError("未知或已被修改的 GSM8K 题面")
        output = json.loads(await self.content.read_text(result.output))
        if (
            output.get("task_id") != task.task_id
            or output.get("solution_id") != solution.solution_id
        ):
            raise ValueError("任务产物与评测版本不匹配")
        response = output.get("response")
        answer = final_answer(response) if isinstance(response, str) else None
        passed = answer is not None and answer == final_answer(self.expected[task.task_id])
        return VerificationResult(
            task.task_id,
            result.trajectory.trajectory_id,
            passed,
            float(passed),
            {"correct": float(passed)},
            "正确"
            if passed
            else "答案错误"
            if answer is not None
            else "缺少有效的 #### NUMBER 最终行",
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="下载固定修订版 GSM8K，准备研究用 TRAIN/VALIDATE")
    parser.add_argument("--directory", type=Path, required=True)
    args = parser.parse_args()
    with urllib.request.urlopen(
        f"{SOURCE}/grade_school_math/data/train.jsonl", timeout=60
    ) as response:
        raw = response.read(16 * 1024 * 1024)
    if hashlib.sha256(raw).hexdigest() != TRAIN_SHA256:
        raise ValueError("官方 GSM8K 文件摘要不匹配，拒绝使用")
    with urllib.request.urlopen(f"{SOURCE}/LICENSE", timeout=30) as response:
        license_text = response.read()
    prepare_dataset(raw, args.directory, source=f"openai/grade-school-math@{REVISION}")
    (args.directory / "LICENSE").write_bytes(license_text)
    print(f"GSM8K 已准备：{args.directory.resolve()}（官方 test 未用于自迭代）")


if __name__ == "__main__":
    main()
