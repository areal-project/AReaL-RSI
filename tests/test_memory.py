from areal_rsi.memory import Memory


def test_memory_record_and_filter_context(tmp_path):
    memory = Memory(tmp_path)

    memory.record("first", "base event", metadata={"ckpt": "ckpt_000", "run_id": "run_1"})
    memory.record("second", "next event", metadata={"ckpt": "ckpt_001", "run_id": "run_2"})

    context = memory.get_context(ckpt="ckpt_000")

    assert len(context["events"]) == 1
    assert context["events"][0]["title"] == "first"
