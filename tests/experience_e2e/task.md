# Transactional reactive data graph

Implement `graph.py` using only Python's standard library. This is the data engine behind an editable dashboard: derived values must remain correct when users change several related rows together.

Expose `ReactiveGraph(nodes)`, `apply(changes) -> set[str]`, `value(node_id) -> float`, and `values() -> dict[str, float]`.

- `nodes` and `changes` are mappings with nonempty string IDs. A definition is exactly `{"value": finite_number}` or `{"op": op, "deps": [id, ...]}`. Supported operations are `sum`, `mean`, `min`, `max`; dependency lists must be nonempty. Duplicated dependencies are legal and count repeatedly. Booleans, nonfinite numbers, missing/extra fields, invalid operations, invalid IDs and invalid dependency containers are rejected with `ValueError`.
- `apply` upserts definitions; `None` deletes an existing node. Deleting an unknown node is an error. The complete post-batch graph determines validity: references may target nodes added later in the same batch; deleting a node and its dependants together is allowed. All remaining references must exist and the graph must be acyclic. A self-reference is a cycle.
- A rejected constructor or batch raises `ValueError`. A rejected batch must leave definitions, values, dependency indexes and future behavior unchanged. Reject calculations that produce nonfinite results as well.
- `apply` returns exactly IDs added, removed, or whose computed numeric value changed. A changed definition whose value stays equal is not reported, but its new dependencies must govern future updates. An empty batch is a no-op. `value` raises `KeyError` for an unknown ID; `values` returns a fresh dictionary. Returned values are floats.
- Never retain mutable aliases to caller-owned definitions/dependency lists. Construction and updates must handle a chain of 6,000 nodes without recursion errors.
- `value` is O(1). For small edits in a mostly independent large graph, validate/recompute affected dependencies without repeatedly scanning/copying the entire graph. A benchmark uses 20,000 nodes and 1,000 edits to one source with a tiny affected region, and allows 3 seconds for the edits, excluding construction. Favor clear code over micro-optimization.

Include tests you find useful and run them. Do not change this specification. Finish when you have concrete validation evidence, or report the remaining limitation. Do not read files outside this workspace except the explicitly provided experience documents and installed tools.
