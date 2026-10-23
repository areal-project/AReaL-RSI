"""Judge-only cocotb compatibility; official test sources stay unchanged."""

from __future__ import annotations

import os
from collections.abc import Mapping

from cocotb_tools.runner import Icarus, Verilator

_parameter_options = Icarus._get_parameter_options


def parameter_options(self: Icarus, parameters: Mapping[str, object]) -> list[str]:
    # Python bool is an int subclass, but its string form is invalid Verilog.
    normalized = {
        name: int(value) if isinstance(value, bool) else value for name, value in parameters.items()
    }
    return _parameter_options(self, normalized)


Icarus._get_parameter_options = parameter_options

if os.environ.get("CVDP_ENABLE_SVA") == "1":
    _build_command = Verilator._build_command

    def build_with_assertions(self: Verilator) -> list[list[str]]:
        commands = _build_command(self)
        # Keep inherited concurrent assertions enabled when selecting an OSS
        # simulator that can parse them. Do not disable assertions or warnings.
        commands[0].insert(2, "--assert")
        return commands

    Verilator._build_command = build_with_assertions
