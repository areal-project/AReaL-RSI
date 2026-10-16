"""Seeded multi-step ledger arithmetic with exact per-step rounding."""

from __future__ import annotations

import json
import random
from collections.abc import Sequence

from areal_rsi.api.base import BenchmarkSplit, Task
from areal_rsi.base.storage import FileContentStore
from areal_rsi.modules.structured_arithmetic import StructuredArithmeticVerifier

RULES = (
    "All money is integer cents; process records in the listed order. "
    "R(x/y) means exact nearest-integer rounding, with halves rounded UP; all fee numerators "
    "are nonnegative. For each row: gross = quantity * unit_cents; "
    "net = R(gross * (10000-discount_bps) / 10000); tax = R(net * tax_bps / 10000); "
    "service_fee = R(gross * service_bps / 10000). For sale add net+tax to balance; "
    "for refund subtract net+tax. Then ALWAYS subtract service_fee, including refunds. "
    "If balance is now negative, subtract R(abs(balance) * overdraft_bps / 10000). "
    "After every fifth record (one-based), subtract settlement_fee_cents. "
    "Do not merge or defer rounding and do not reorder these steps. "
    "Return ONLY JSON with answer (final balance as an exact decimal integer STRING) and "
    "checksum (the nonnegative integer balance modulo 97). No markdown."
)


def rounded(numerator: int, denominator: int = 10000) -> int:
    return (2 * numerator + denominator) // (2 * denominator)


def compute(data: dict[str, object]) -> int:
    balance = int(str(data["initial_cents"]))
    rows = data["records"]
    assert isinstance(rows, list)
    for index, row in enumerate(rows, 1):
        gross = row["quantity"] * row["unit_cents"]
        net = rounded(gross * (10000 - row["discount_bps"]))
        tax = rounded(net * row["tax_bps"])
        fee = rounded(gross * row["service_bps"])
        balance += (net + tax) * (1 if row["kind"] == "sale" else -1) - fee
        if balance < 0:
            balance -= rounded(abs(balance) * int(str(data["overdraft_bps"])))
        if index % 5 == 0:
            balance -= int(str(data["settlement_fee_cents"]))
    return balance


class LedgerBenchmark:
    name = "ledger-arithmetic-v1"

    def __init__(self, content: FileContentStore, *, seed: int = 20261019):
        self.content, self.seed = content, seed
        self.expected: dict[str, tuple[str, int]] = {}
        self.tasks: dict[BenchmarkSplit, list[Task]] = {}

    async def list_tasks(self, split: BenchmarkSplit) -> Sequence[Task]:
        if split in self.tasks:
            return self.tasks[split]
        rng = random.Random(f"ledger/{self.seed}/{split.value}")
        result = []
        for index in range(60 if split == BenchmarkSplit.FINAL else 30):
            data: dict[str, object] = {
                "instructions": RULES,
                "initial_cents": rng.randrange(10**13, 10**15),
                "overdraft_bps": rng.choice([13, 37, 125, 333]),
                "settlement_fee_cents": rng.randrange(10**9, 10**11),
                "records": [
                    {
                        "kind": rng.choice(["sale", "refund", "refund"]),
                        "quantity": rng.randrange(3, 101),
                        "unit_cents": rng.randrange(10**11, 10**13),
                        "discount_bps": rng.choice([125, 333, 500, 1250, 2500]),
                        "tax_bps": rng.choice([375, 500, 750, 1250]),
                        "service_bps": rng.choice([13, 37, 125, 250]),
                    }
                    for _ in range(24)
                ],
            }
            answer = compute(data)
            task_id = f"ledger-{self.seed}-{split.value}-{index}"
            self.expected[task_id] = str(answer), answer % 97
            public = await self.content.put_json(json.loads(json.dumps(data)))
            result.append(Task(task_id, public))
        self.tasks[split] = result
        return result


class LedgerVerifier(StructuredArithmeticVerifier):
    def __init__(self, content: FileContentStore, benchmark: LedgerBenchmark):
        self.content, self.expected = content, benchmark.expected
