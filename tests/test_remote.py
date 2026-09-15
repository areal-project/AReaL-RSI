"""Remote provider selection is independent from any research workflow."""

from pathlib import Path

import pytest

from areal_rsi.modules.remote import registry
from areal_rsi.modules.remote.base import RemoteBackend, RemoteContext, RemoteJob


class OtherProvider:
    environment_names = ("OTHER_REMOTE_TOKEN",)

    async def create(self, context: RemoteContext, config_path: Path) -> RemoteBackend:
        raise AssertionError("Read-only inspection must not start a backend")

    def read_jobs(self, root: Path, run_id: str) -> list[RemoteJob]:
        return [
            RemoteJob("job-" + run_id, "running", {"total": 1}, "https://remote.example/jobs/1")
        ]


def test_provider_selection_and_read_only_jobs_do_not_require_old_app(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = OtherProvider()
    monkeypatch.setattr(registry, "PROVIDERS", {"other": provider})
    monkeypatch.setenv("RSI_REMOTE_BACKEND", "other")
    monkeypatch.setenv("RSI_REMOTE_CONFIG", str(tmp_path / "remote.json"))
    monkeypatch.delenv("RSI_MODE", raising=False)
    assert registry.get_provider("other") is provider
    settings = registry.RemoteSettings.from_environment()
    assert settings.backend == "other"
    assert registry.read_remote_jobs(tmp_path, "research1")[0].job_id == "job-research1"
    assert registry.all_remote_environment_names() == ("OTHER_REMOTE_TOKEN",)
    with pytest.raises(ValueError, match=r"unknown|未知"):
        registry.get_provider("missing")


@pytest.mark.parametrize("url", ["file:///tmp/private", "https://name:key@example.test"])
def test_remote_job_rejects_unsafe_links(url: str) -> None:
    with pytest.raises(ValueError):
        RemoteJob("job", "running", {}, url)
