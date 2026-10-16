---
name: evidence-guided-interactive-state-skill
version: 1.0.0
description: Build and validate small interactive task-style interfaces from behavioral evidence using an explicit state model, stable selectors, persistence boundaries, and scenario-based checks.
scope: Applies to browser or UI tasks whose evidence specifies add/edit/delete, completion, filtering, persistence, validation, accessibility, and responsive behavior. It is a reusable implementation and validation method, not a product template.
dependencies:
  - python3
---

# Evidence-guided interactive state skill

## Purpose

Turn behavioral evidence into a small, testable interactive application without copying product source, business data, credentials, or test answers. The available evidence demonstrates a task-list style interface with CRUD behavior, completion state, filters, local persistence, empty-input handling, literal text rendering, stable `data-testid` hooks, and responsive layout. It also shows that a first implementation can appear plausible while failing validation, so the method below treats validation as part of implementation rather than a final inspection.

## Transferable implementation steps

1. **Extract observable contracts.** Make a table of user action, expected visible result, state mutation, persistence requirement, and selector/accessibility hook. Separate facts supported by evidence from assumptions.
2. **Define a minimal state model.** Use records with an opaque unique id, user-visible title, and boolean completion flag. Keep the active filter and transient edit id separate from durable records. Do not encode business-specific meanings into this model.
3. **Define transitions before UI code.** Specify add, toggle, begin-edit, save, delete, and filter transitions. Trim input at the boundary; reject empty titles without mutating state. Save only validated edits.
4. **Centralize rendering.** Derive the visible collection from durable state plus the selected filter. Render an explicit empty state for both an empty collection and a filter with no matches. Scope row actions by record id.
5. **Choose stable, semantic hooks.** Use labels, buttons, native checkbox behavior, and stable test ids for externally observed controls. Avoid selectors tied to generated classes or visual position.
6. **Persist at a clear boundary.** Load once with defensive parsing and schema checks; persist after every durable mutation. Treat storage failures as recoverable rather than allowing them to crash the interaction loop.
7. **Render user text as text.** Use text-node assignment or an equivalent escaping mechanism. Never interpret titles as markup.
8. **Design for narrow layouts.** Allow long titles to wrap, let action controls flow, avoid fixed-width overflow, and preserve usable hit targets. Validate at a representative narrow viewport if the environment supports it.
9. **Validate scenarios, not only snapshots.** Exercise sequences that cross state boundaries: add two records, toggle one, filter, edit the correct row, delete, reload, and submit whitespace. Check both visible output and durable state.
10. **Use failure evidence diagnostically.** When a check fails, classify it as state, selector, persistence, boundary, rendering-safety, or layout failure; fix the underlying contract rather than adding a one-off assertion workaround.

## Applicability

Use this skill for small CRUD dashboards, checklists, notes, queues, bookmarks, and other interfaces where correctness is primarily observable through user actions and state transitions. It is especially useful when requirements are sparse but validation evidence names behaviors and selectors.

## Exclusions

- Do not reuse the demonstrated product's HTML, CSS, identifiers beyond generic patterns, wording, data, or implementation code.
- Do not infer hidden business rules from one implementation or one production run.
- Do not use this skill as a substitute for authorization, threat modeling, regulated-data controls, or multi-user conflict resolution.
- Do not assume browser `localStorage` is sufficient for security-sensitive or multi-device persistence.
- Do not claim generalization from a single validation run; repeat scenarios across relevant variants.

## Dependencies and interfaces

The packaged validator requires only `python3` and the Python standard library. A target package may expose a `TaskStore` class in `package/task_state.py` with `add(title)`, `toggle(id)`, `edit(id, title)`, `delete(id)`, `filter(kind)`, and `load/save`-equivalent behavior, but this is an optional generic adapter, not a required product API. The validator's self-test uses an in-memory synthetic implementation when no package adapter is supplied.

## Business-neutral validation template

Parameterize these values for a future task:

- `record_label`: singular noun shown to users (for example, `item`)
- `title_field`: user-visible text field name
- `filters`: `all`, `active`, `completed` or an equivalent partition
- `storage_key`: namespaced persistence key
- `selector_map`: semantic selectors for add, row, edit, save, delete, toggle, and filters
- `viewport`: representative narrow width and height
- `scenarios`: ordered action sequences and expected state predicates

Keep expected answers out of the template. Assert invariants such as “whitespace-only input does not increase record count,” “editing one id does not change another,” “filtering does not mutate durable records,” and “reload preserves durable records.”

## Validation and failure modes

Validation should cover runtime startup, add/count, empty-input boundary, row-scoped edit, completion toggle, filter partitioning, deletion, persistence round-trip, literal text safety, and narrow-layout overflow. A failure in one dimension should not be hidden by a passing aggregate score.

Common failure modes include: implementing only add while omitting transitions; editing the first row instead of the selected id; persisting only on initial load; accepting whitespace titles; using unsafe HTML insertion; losing state on malformed storage; selectors that change with styling; and fixed-width rows that overflow on mobile. The accompanying checklist and `scripts/validate.py` encode these checks with synthetic data.

## Evidence boundary

The source evidence showed a completed validation aggregate for one task-desk implementation and an earlier failed training attempt. This skill extracts the workflow lessons and generic invariants only. It does not treat those results as proof that every future implementation will pass.
