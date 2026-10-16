"""Independent held-out checks. This file is never copied into an agent workspace."""

from __future__ import annotations

import importlib.util
import math
import random
import time
from dataclasses import asdict, dataclass
from functools import partial
from pathlib import Path
from typing import Any


@dataclass
class Score:
    passed: list[str]
    failures: dict[str, str]
    update_seconds: float | None = None


def grade(path: Path) -> Score:
    result = Score([], {})
    spec = importlib.util.spec_from_file_location("candidate", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    import sys

    sys.modules["candidate"] = module
    spec.loader.exec_module(module)
    graph = module.ReactiveGraph

    def check(name: str, run: Any) -> None:
        try:
            run()
            result.passed.append(name)
        except Exception as exc:
            result.failures[name] = f"{type(exc).__name__}: {str(exc)[:200]}"

    def reject(run: Any) -> None:
        try:
            run()
        except ValueError:
            return
        raise AssertionError("Invalid operation was accepted")

    def operations() -> None:
        g = graph(
            {
                "a": {"value": 3},
                "b": {"value": -1},
                **{op: {"op": op, "deps": ["a", "b", "a"]} for op in ("sum", "mean", "min", "max")},
            }
        )
        assert g.values() == {
            "a": 3.0,
            "b": -1.0,
            "sum": 5.0,
            "mean": 5 / 3,
            "min": -1.0,
            "max": 3.0,
        }
        assert all(type(x) is float for x in g.values().values())
        assert g.apply({}) == set()
        try:
            g.value("missing")
        except KeyError:
            pass
        else:
            raise AssertionError("Unknown value did not raise KeyError")

    def post_batch() -> None:
        g = graph({"a": {"value": 1}, "b": {"op": "sum", "deps": ["a"]}})
        assert g.apply({"b": {"op": "sum", "deps": ["c"]}, "c": {"value": 8}, "a": None}) == {
            "a",
            "b",
            "c",
        }
        assert g.values() == {"b": 8.0, "c": 8.0}
        assert g.apply({"c": None, "b": None}) == {"b", "c"}
        assert g.values() == {}

    def rollback() -> None:
        g = graph({"a": {"value": 1}, "b": {"op": "sum", "deps": ["a"]}})
        for patch in (
            {"a": {"value": 7}, "b": {"op": "sum", "deps": ["absent"]}},
            {"a": None},
            {"unknown": None},
            {"a": {"op": "sum", "deps": ["b"]}},
        ):
            reject(partial(g.apply, patch))
            assert g.values() == {"a": 1.0, "b": 1.0}
        assert g.apply({"a": {"value": 4}}) == {"a", "b"}
        assert g.value("b") == 4

    def same_value_rewire() -> None:
        g = graph({"x": {"value": 1}, "y": {"value": 1}, "z": {"op": "sum", "deps": ["x"]}})
        assert g.apply({"z": {"op": "sum", "deps": ["y"]}}) == set()
        assert g.apply({"x": {"value": 8}}) == {"x"}
        assert g.apply({"y": {"value": 9}}) == {"y", "z"}
        assert g.value("z") == 9

    def aliases() -> None:
        nodes: dict[str, Any] = {"a": {"value": 2}, "b": {"op": "sum", "deps": ["a"]}}
        g = graph(nodes)
        nodes["a"]["value"] = 100
        nodes["b"]["deps"].append("missing")
        assert g.values() == {"a": 2.0, "b": 2.0}
        updates: dict[str, Any] = {"b": {"op": "sum", "deps": ["a", "a"]}}
        g.apply(updates)
        updates["b"]["deps"].clear()
        returned = g.values()
        returned["a"] = 100
        g.apply({"a": {"value": 3}})
        assert g.values() == {"a": 3.0, "b": 6.0}

    def invalid_shapes() -> None:
        invalid = [
            None,
            {},
            {"value": True},
            {"value": math.nan},
            {"value": math.inf},
            {"value": "1"},
            {"value": 1, "extra": 2},
            {"op": "sum", "deps": []},
            {"op": "sum", "deps": "a"},
            {"op": "bad", "deps": ["a"]},
            {"op": "sum", "deps": [1]},
            {"op": "sum", "deps": ["a"], "extra": 2},
        ]
        for node in invalid:
            reject(partial(graph, {"a": {"value": 1}, "x": node}))
        for key in ("", 1, None):
            reject(partial(graph, {key: {"value": 1}}))

    def cycle_batches() -> None:
        reject(
            lambda: graph({"a": {"op": "sum", "deps": ["b"]}, "b": {"op": "sum", "deps": ["a"]}})
        )
        g = graph({"a": {"value": 1}, "b": {"value": 2}})
        reject(
            lambda: g.apply({"a": {"op": "sum", "deps": ["b"]}, "b": {"op": "sum", "deps": ["a"]}})
        )
        assert g.values() == {"a": 1.0, "b": 2.0}
        g.apply({"b": {"op": "sum", "deps": ["a"]}})
        assert g.apply({"a": {"op": "sum", "deps": ["b"]}, "b": {"value": 3}}) == {"a", "b"}

    def overflow() -> None:
        g = graph({"a": {"value": 1.0}, "b": {"op": "sum", "deps": ["a", "a"]}})
        reject(lambda: g.apply({"a": {"value": 1e308}}))
        assert g.values() == {"a": 1.0, "b": 2.0}
        assert g.apply({"a": {"value": 2}}) == {"a", "b"}

    def deep_chain() -> None:
        nodes = {
            "n0": {"value": 1},
            **{f"n{i}": {"op": "sum", "deps": [f"n{i - 1}"]} for i in range(1, 6000)},
        }
        g = graph(nodes)
        assert g.value("n5999") == 1
        assert len(g.apply({"n0": {"value": 2}})) == 6000
        assert g.value("n5999") == 2

    def randomized() -> None:
        rng = random.Random(19027)
        nodes: dict[str, Any] = {f"n{i}": {"value": rng.randint(-8, 8)} for i in range(12)}
        for i in range(12, 90):
            nodes[f"n{i}"] = {
                "op": rng.choice(["sum", "min", "max", "mean"]),
                "deps": [f"n{rng.randrange(i)}" for _ in range(rng.choice([1, 2, 4]))],
            }

        def oracle() -> dict[str, float]:
            values: dict[str, float] = {}
            for i in range(90):
                node = nodes[f"n{i}"]
                if "value" in node:
                    value = float(node["value"])
                else:
                    args = [values[d] for d in node["deps"]]
                    value = {
                        "sum": sum(args),
                        "mean": sum(args) / len(args),
                        "min": min(args),
                        "max": max(args),
                    }[node["op"]]
                values[f"n{i}"] = value
            return values

        g = graph(nodes)
        before = oracle()
        for _ in range(100):
            patch = {}
            for i in rng.sample(range(90), 6):
                patch[f"n{i}"] = (
                    {"value": rng.randint(-8, 8)}
                    if i < 12
                    else {
                        "op": rng.choice(["sum", "min", "max", "mean"]),
                        "deps": [f"n{rng.randrange(i)}" for _ in range(rng.choice([1, 2, 4]))],
                    }
                )
            nodes.update(patch)
            after = oracle()
            assert g.apply(patch) == {key for key in after if after[key] != before[key]}
            assert g.values() == after
            before = after

    def incremental_cost() -> None:
        g = graph(
            {
                **{f"n{i}": {"value": 0} for i in range(20000)},
                "dependent": {"op": "sum", "deps": ["n0"]},
            }
        )
        started = time.perf_counter()
        for i in range(1000):
            g.apply({"n0": {"value": i}})
        result.update_seconds = time.perf_counter() - started
        assert g.value("dependent") == 999
        assert result.update_seconds < 3, "Small updates repeatedly process unrelated state"

    for function in (
        operations,
        post_batch,
        rollback,
        same_value_rewire,
        aliases,
        invalid_shapes,
        cycle_batches,
        overflow,
        deep_chain,
        randomized,
        incremental_cost,
    ):
        check(function.__name__, function)
    return result


if __name__ == "__main__":
    import json
    import sys

    print(json.dumps(asdict(grade(Path(sys.argv[1])))))
