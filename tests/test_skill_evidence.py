"""Resource discovery does not establish that a Skill resource was read or run."""

from typing import Any

from areal_rsi.modules.runtime_package import skill_resource_events


def test_resource_use_excludes_directory_listing_failed_and_unverified_commands() -> None:
    events: list[dict[str, Any]] = []
    root = "research-skills/example/1.0.0/"
    for ident, name, arguments, value in [
        ("list", "workspace.list", {"path": root}, {"entries": []}),
        ("entry", "workspace.read", {"path": root + "SKILL.md"}, {"text": "method"}),
        ("read", "workspace.read", {"path": root + "references/checklist.md"}, {"text": "check"}),
        (
            "run",
            "workspace.run",
            {"argv": ["python3", root + "scripts/validate.py"]},
            {"exitCode": 0},
        ),
        (
            "bad",
            "workspace.run",
            {"argv": ["python3", root + "scripts/validate.py"]},
            {"exitCode": 1},
        ),
        ("unknown", "workspace.run", {"argv": ["python3", root + "scripts/validate.py"]}, None),
    ]:
        events.append(
            {
                "type": "tool.requested",
                "data": {"callId": ident, "name": name, "arguments": arguments},
            }
        )
        events.append(
            {
                "type": "tool.completed",
                "data": {"callId": ident, "name": name, "value": value, "error": None},
            }
        )
    calls = skill_resource_events(events)
    assert {key: len(value) for key, value in calls.items()} == {
        "reads": 1,
        "commands": 1,
        "discovery": 1,
        "entrypoints": 1,
        "failed": 1,
        "unverified": 1,
    }
