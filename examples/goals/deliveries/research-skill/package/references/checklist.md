# Evidence-guided UI checklist

## Contract extraction
- [ ] List observable actions and outcomes before writing UI code.
- [ ] Distinguish durable state, derived view state, and transient editing state.
- [ ] Record stable semantic selectors and accessible names.

## State and boundaries
- [ ] Add trims input and rejects empty/whitespace-only values.
- [ ] Every record has an opaque id; actions target that id.
- [ ] Toggle changes only the selected record.
- [ ] Save validates the edited title and preserves the record id.
- [ ] Delete removes only the selected record.
- [ ] Filtering is derived and does not mutate durable state.

## Persistence and safety
- [ ] Load is defensive against missing or malformed data.
- [ ] Durable mutations are persisted at a defined boundary.
- [ ] Reload restores records and completion flags.
- [ ] User text is rendered as text, never interpreted as markup.

## UI and responsive behavior
- [ ] Empty collection and empty-filter results are explicit.
- [ ] Controls have usable labels and stable hooks.
- [ ] Long titles wrap without horizontal overflow.
- [ ] Row actions remain associated with their row on narrow screens.

## Scenario validation
- [ ] Add two synthetic records.
- [ ] Reject a whitespace-only title.
- [ ] Toggle one record and verify the other is unchanged.
- [ ] Check all/active/completed partitions.
- [ ] Edit one record and verify row scoping.
- [ ] Delete one record and verify count.
- [ ] Round-trip through persistence.
- [ ] Run the validator with `python3 scripts/validate.py --self-test`.
- [ ] Investigate every non-zero exit code before declaring success.
