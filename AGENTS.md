# Development constraints

- Put contracts in `api/`, core implementations in `infra/`, and optional entry points in `ui/`. Do not add top-level base/modules/app layers.
- Put data contracts in `api/records.py`, `api/research.py`, and `api/execution.py`; protocols in `api/interfaces.py`; and control requests in `api/control.py`.
- The system entry point is `ResearchSystem` in `infra/research.py`. API must not import infra, and infra must not import UI.
- Prefer removing duplicate logic and merging thin modules. Do not add layers for directory symmetry, hypothetical extensions, or shorter files.
- Do not add injection interfaces with only one implementation, forwarding classes, empty lifecycle methods, or compatibility wrappers. Before introducing an abstraction, explain the concrete reuse it serves.
- Use named dataclasses and enums for fixed domain structures. Do not use nested `dict[str, Any]` structures for internal state, retries, or results. Dictionaries are only for ID-based indexing, homogeneous mappings, or JSON/plugin boundaries. After decoding at a boundary, use typed fields; do not add dictionary compatibility wrappers.
- Share JSON validation through `api/control.py`. Named control methods must accept `run_id` and retain domain authorization and state checks.
- Data directories, candidate digests, frozen protocols, and source-lock records are not source directories. Do not rewrite these records as a side effect of moving packages.
- When moving the runtime, update package copying, plugin entry points, resource lookup, and the sandbox deny scope covering the entire source package together.
- Test real behavior and failures, rather than exhaustively checking fields and forwarding layers. For changes across modules, run `make check` and report any environments skipped.

Keep state, scheduling, and records at the top level of `coordination`; put policies in `policies`, Agent tools in `tools`, and research evaluation in `evaluation`. Do not add more one-off helper modules at the top level.
