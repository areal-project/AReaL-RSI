"""Evaluate arithmetic only; no attributes, imports, names, or function calls."""

import ast
import operator
import sys
from collections.abc import Callable
from typing import Any, cast

OPS: dict[type[ast.AST], Callable[..., Any]] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}


def calculate(node: ast.AST) -> int | float:
    if isinstance(node, ast.Constant) and type(node.value) in (int, float):
        return cast(int | float, node.value)
    if isinstance(node, ast.BinOp) and type(node.op) in OPS:
        return OPS[type(node.op)](calculate(node.left), calculate(node.right))  # type: ignore[no-any-return]
    if isinstance(node, ast.UnaryOp) and type(node.op) in OPS:
        return OPS[type(node.op)](calculate(node.operand))  # type: ignore[no-any-return]
    raise ValueError("Only arithmetic is supported")


if __name__ == "__main__":
    expression = sys.argv[1]
    if len(expression) > 1000:
        raise ValueError("Expression too long")
    print(calculate(ast.parse(expression, mode="eval").body))
