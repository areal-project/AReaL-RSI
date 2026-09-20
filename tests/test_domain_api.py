"""Reusable domain contracts remain independent of the removed orchestration API."""

from pathlib import Path

from areal_rsi.api.records import ArtifactChange, ModelChange, ResearchChange
from areal_rsi.infra.storage.catalog import LocalModelCatalog
from areal_rsi.infra.storage.files import FileContentStore, FileVersionManager, decode, encode


def test_domain_changes_and_catalog_reuse_immutable_versions(tmp_path: Path) -> None:
    import asyncio

    async def scenario() -> None:
        content = FileContentStore(tmp_path / "content")
        versions = FileVersionManager(tmp_path / "versions")
        catalog = LocalModelCatalog(tmp_path / "catalog", versions)
        data = await content.put_text("training data")
        for change in (ModelChange(data, {"steps": 10}, "train"),
                       ArtifactChange("kernel", data, "edit")):
            assert decode(ResearchChange, encode(change)) == change
        first = await catalog.model("target", data)
        assert await catalog.model("target", data) == first
        other = await catalog.model("target", await content.put_text("new weights"))
        assert other.version_id != first.version_id
        baseline = await catalog.baseline(first, data)
        assert await catalog.baseline(first, data) == baseline
        assert await versions.get_model(first.version_id) == first

    asyncio.run(scenario())
