"""Event append ordering and recovery across independent store instances."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from areal_rsi.base.storage import write_json
from areal_rsi.coordination.store import ResearchStore


def test_independent_writers_allocate_unique_events(tmp_path: Path) -> None:
    def append(value: int) -> int:
        return ResearchStore(tmp_path, "events").event("observed", value=value).sequence

    with ThreadPoolExecutor(max_workers=8) as pool:
        sequences = list(pool.map(append, range(40)))
    assert sorted(sequences) == list(range(1, 41))
    events = ResearchStore(tmp_path, "events").events()
    assert {event.data["value"] for event in events} == set(range(40))


@pytest.mark.parametrize("index_state", ["missing", "corrupt", "stale", "ahead"])
def test_event_index_recovers_without_losing_committed_tail(
    tmp_path: Path, index_state: str
) -> None:
    store = ResearchStore(tmp_path, "events")
    store.event("first")
    store.event("second")
    index = store.root / "coordination" / "event-index.json"
    if index_state == "missing":
        index.unlink()
    elif index_state == "corrupt":
        index.write_text("{")
    else:
        write_json(index, {"sequence": 1 if index_state == "stale" else 9})
    reopened = ResearchStore(tmp_path, "events")
    assert [event.kind for event in reopened.events(after=1)] == ["second"]
    assert reopened.event("third").sequence == 3
    assert [event.kind for event in reopened.events()] == ["first", "second", "third"]


def test_indexed_append_and_incremental_read_do_not_scan_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = ResearchStore(tmp_path, "events")
    store.event("first")

    def forbidden_scan(*args: object, **kwargs: object) -> None:
        raise AssertionError("Indexed event operations must not scan the directory")

    monkeypatch.setattr(Path, "glob", forbidden_scan)
    reopened = ResearchStore(tmp_path, "events")
    assert reopened.event("second").sequence == 2
    assert [event.kind for event in reopened.events(after=1)] == ["second"]
    assert reopened.events(after=2) == []


def test_event_write_survives_index_write_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = ResearchStore(tmp_path, "events")
    store.event("first")

    def interrupted_index(path: Path, value: object, *, immutable: bool = False) -> None:
        if path.name == "event-index.json":
            raise OSError("Interrupted after the event committed")
        write_json(path, value, immutable=immutable)

    with monkeypatch.context() as patcher:
        patcher.setattr("areal_rsi.coordination.store.write_json", interrupted_index)
        with pytest.raises(OSError):
            store.event("second")
    recovered = ResearchStore(tmp_path, "events")
    assert [event.kind for event in recovered.events()] == ["first", "second"]
    assert recovered.event("third").sequence == 3
