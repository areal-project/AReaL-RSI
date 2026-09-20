"""Reusable domain contracts remain independent of the removed orchestration API."""

from pathlib import Path

import pytest

from areal_rsi.api.base import ArtifactChange, EvolutionTarget, ModelChange, ResearchChange
from areal_rsi.base.catalog import LocalModelCatalog
from areal_rsi.base.storage import FileContentStore, FileVersionManager, decode, encode
from areal_rsi.coordination.config import config_from_dict


@pytest.mark.parametrize("target", list(EvolutionTarget))
def test_research_target_roundtrips_without_selecting_a_workflow(target: EvolutionTarget) -> None:
    config = config_from_dict({
        "brief": {"objective": "test", "instructions": "test"},
        "evolution_target": target.value,
    })
    assert config.evolution_target is target
    assert config_from_dict(config.public()).evolution_target is target
    assert "workflow" not in config.public()
    assert config.brief.limits.max_model_calls is None
    with pytest.raises(ValueError):
        config_from_dict({**config.public(), "evolution_target": "unknown"})


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
