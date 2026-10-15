# GPU kernel development

First identify the operation, shape/layout assumptions, accumulation dtype and
numerical tolerances. Select an implementation that computes the full result for
valid inputs rather than memorizing input values. Check edge cases such as
non-divisible tile dimensions and reductions before optimizing memory access.

Keep ModelNew initialization compatible with the supplied Model. Put kernel
definitions and imports in model_new.py so it can be evaluated in a clean process.
Explain untested assumptions in the final task summary. Never invent timings.
