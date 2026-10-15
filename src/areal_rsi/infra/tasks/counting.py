"""One fixed combinatorics problem for exercising harness iteration locally."""

from __future__ import annotations

from collections.abc import Sequence
from functools import cache

from areal_rsi.api.records import BenchmarkSplit, Task

QUESTION = """Consider lattice paths of 36 steps from (0, 0) to (36, 0).
Each step is U=(1, 1) or D=(1, -1). Every vertex must have height between
0 and 6 inclusive. There must be exactly 5 peaks, where a peak is an adjacent
pair UD. Define A as the sum of the heights BEFORE each of the 36 steps
(the initial height is included; the final height is not).
How many such paths have A divisible by 7? Give the exact integer, with a
derivation or a reproducible computation. End with one line: #### NUMBER"""


def count_paths(pairs: int = 18, ceiling: int = 6, peaks: int = 5, modulus: int = 7) -> int:
    """Host-only exact oracle; residues keep the state space small."""
    if pairs < 0 or ceiling < 0 or peaks < 0 or modulus <= 0:
        raise ValueError("Invalid path-counting parameters")

    @cache
    def count(steps: int, height: int, used: int, last_up: bool, residue: int) -> int:
        if used > peaks or height > steps:
            return 0
        if steps == 0:
            return int(height == 0 and used == peaks and residue == 0)
        residue = (residue + height) % modulus
        total = 0
        if height < ceiling:
            total += count(steps - 1, height + 1, used, True, residue)
        if height > 0:
            total += count(steps - 1, height - 1, used + int(last_up), False, residue)
        return total

    return count(2 * pairs, 0, 0, False, 0)


class CountingBenchmark:
    name = "bounded-paths-v1"

    async def list_tasks(self, split: BenchmarkSplit) -> Sequence[Task]:
        # Deliberately the SAME problem: final is a fresh execution, not a holdout.
        return [Task("bounded_paths", {"question": QUESTION})]
