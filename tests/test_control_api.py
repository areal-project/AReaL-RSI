"""The same command boundary protects local clients and the HTTP workbench."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from areal_rsi.api.research import TaskBrief
from areal_rsi.infra.coordination.config import ResearchConfig
from areal_rsi.infra.coordination.coordinator import Coordinator
from areal_rsi.infra.research import ResearchSystem
from areal_rsi.infra.storage.files import write_json


@pytest.mark.parametrize("action,params", [
    ("misspelled-action", {}),
    ("create", {"simulated": "false"}),
    ("create", {"unknown": "private-sentinel"}),
    ("retry", {"run_id": "missing"}),
    ("detail", {"run_id": "missing", "event_limit": 1001}),
])
def test_invalid_requests_are_rejected_before_lookup_or_mutation(
    tmp_path: Path, action: str, params: dict[str, Any],
) -> None:
    root = tmp_path / "application"
    with pytest.raises(ValueError) as error:
        asyncio.run(ResearchSystem(root).call(action, params))
    assert "private-sentinel" not in str(error.value)
    assert not root.exists()


def test_direct_stop_and_dispatch_share_operator_reason(tmp_path: Path) -> None:
    async def scenario() -> None:
        system = ResearchSystem(tmp_path)
        config = ResearchConfig(TaskBrief("Research", "Inspect contracts"))
        for run_id in ("direct", "dispatch"):
            c = Coordinator.create(tmp_path, config.brief, run_id=run_id)
            write_json(c.store.root / "coordination/config.json", config.public())
        await system.stop("direct", reason="operator reason")
        await system.call("stop", {"run_id": "dispatch", "reason": "operator reason"})
        for run_id in ("direct", "dispatch"):
            c = system.coordinator(run_id)
            assert c.state.status == "stopped"
            assert any(event.data.get("reason") == "operator reason" for event in c.store.events())
        await system.close()

    asyncio.run(scenario())


def test_partial_creation_can_retry_without_publishing_an_incomplete_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from runtime_fakes import ActionRuntime

    from areal_rsi.api.research import ResearchMessage
    from areal_rsi.infra import research
    from areal_rsi.infra.storage.files import write_json as persist

    async def scenario() -> None:
        system = ResearchSystem(
            tmp_path, config=ResearchConfig(TaskBrief("test", "test")),
            runtime_factory=lambda _: ActionRuntime("wait"),
        )
        request = {"run_id": "retry-create"}

        def fail_config(path: Path, *args: Any, **kwargs: Any) -> None:
            if path.name == "config.json":
                raise OSError("disk full")
            persist(path, *args, **kwargs)

        try:
            with monkeypatch.context() as patch:
                patch.setattr(research, "write_json", fail_config)
                with pytest.raises(OSError, match="disk full"):
                    await system.create(request)
            assert (await system.list_runs())["runs"] == []
            result = await system.create(request)
            assert result["status"] == "planned"
            assert (await system.create(request))["run_id"] == result["run_id"]
            with pytest.raises(ValueError, match="不同"):
                await system.create({**request, "objective": "different"})
            message_request = {"run_id": "initial-message", "message": "Investigate this"}
            await system.create(message_request)
            await system.stop("initial-message")
            assert (await system.create(message_request))["status"] == "stopped"
            messages = system.coordinator("initial-message").store.records(
                "messages", ResearchMessage
            )
            assert len([message for message in messages if message.sender == "user"]) == 1
        finally:
            await system.close()

    asyncio.run(scenario())


def test_control_ownership_rejects_other_clients_before_mutation(tmp_path: Path) -> None:
    async def scenario() -> None:
        first = ResearchSystem(tmp_path, config=ResearchConfig(TaskBrief("test", "test")))
        second = ResearchSystem(tmp_path)
        try:
            await first.create({"run_id": "shared"})
            async with first.control("shared", "chat") as c:
                before = c.path.read_bytes()
                with pytest.raises(ValueError, match="another live process"):
                    await second.stop("shared")
                assert c.path.read_bytes() == before
                c.user_message("first", key="first")
            async with second.control("shared", "chat") as c:
                c.user_message("second", key="second")
                assert len(c.state.inboxes["interaction"]) == 2
        finally:
            await first.close()
            await second.close()

    asyncio.run(scenario())


def test_invalid_nested_configuration_is_a_client_error(tmp_path: Path) -> None:
    async def scenario() -> None:
        system = ResearchSystem(tmp_path)
        for config in (
            {"brief": {"objective": 123, "instructions": "test"}},
            {"brief": {"objective": "test", "instructions": "test"}, "commands": [{}]},
            {
                "brief": {"objective": "test", "instructions": "test"},
                "model_settings_pinned": "false",
            },
        ):
            with pytest.raises(ValueError, match="Invalid ResearchConfig"):
                await system.create({"config": config})
        assert not (tmp_path / "runs").exists()

    asyncio.run(scenario())


def test_report_download_is_limited_to_published_files(tmp_path: Path) -> None:
    import zipfile

    from areal_rsi.api.execution import JobSpec
    from areal_rsi.api.research import ResearchJob

    async def scenario() -> None:
        c = Coordinator.create(tmp_path, TaskBrief('Review', 'Review'), run_id='reports')
        output = c.store.root / 'execution-outputs' / 'job-report'
        public = output / 'public'
        public.mkdir(parents=True)
        (public / 'report.txt').write_text('Verified evidence')
        private = output / 'private.txt'
        private.write_text('Private verifier input')
        info = ResearchJob('researcher', str(output), JobSpec(), 'now',
                           verification=True, processed=True, public_output_subdir='public')
        c.store.save_jobs({'job-report': info})
        app = ResearchSystem(tmp_path)
        report = await app.export_report('reports', 'job-report')
        with zipfile.ZipFile(report.path) as archive:
            assert set(archive.namelist()) == {'report.txt', 'rsi-verification.json'}
        (public / 'leak.txt').symlink_to(private)
        with pytest.raises(ValueError, match='symbolic'):
            await app.export_report('reports', 'job-report')
        with pytest.raises(ValueError, match='公开'):
            await app.export_report('reports', 'job-other')
        await app.close()

    asyncio.run(scenario())
