"""Durable handoffs and lossless reuse of arbitrary research products."""

import asyncio
from pathlib import Path
from typing import Any

import pytest

from areal_rsi.api.research import EvidenceRecord, ResearchLimits, ResearchMessage, TaskBrief
from areal_rsi.base.storage import now
from areal_rsi.coordination.actions import ResearchActions
from areal_rsi.coordination.coordinator import Coordinator
from areal_rsi.modules.responses import ToolCall


def test_shared_agent_workspace_is_one_explicit_run_directory(tmp_path: Path) -> None:
    c = Coordinator.create(tmp_path, TaskBrief("共享工作区", ""))
    c.state["shared_agent_workspace"] = True
    c.save()
    actions = ResearchActions(c)
    rsi = actions.workspace("rsi")
    researcher = actions.workspace("researcher-1")
    assert rsi == researcher == c.store.root / "shared-workspace"
    (rsi / "change.py").write_text("visible = True\n")
    assert (researcher / "change.py").read_text() == "visible = True\n"


def test_passk_attempts_get_independent_workspaces_even_for_shared_templates(
    tmp_path: Path,
) -> None:
    c = Coordinator.create(tmp_path, TaskBrief("独立探索", ""))
    c.state.update(shared_agent_workspace=True, mode="passk")
    c.save()
    actions = ResearchActions(c)
    first = actions.workspace("researcher-1")
    second = actions.workspace("researcher-2")
    assert first != second
    assert first == c.store.paths.agent("researcher-1") / "workspace"


def test_rsi_handoff_grants_prose_and_message_references_after_recovery(tmp_path: Path) -> None:
    async def scenario() -> None:
        c = Coordinator.create(tmp_path, TaskBrief("研究一般产物", ""))
        c.start()
        actions = ResearchActions(c)
        ref = c.store.put("materials", "observation", {"agent_id": "source", "value": 42})
        extra = c.store.put("materials", "details", {"agent_id": "source", "value": 43})
        private = c.store.put("materials", "unshared", {"agent_id": "source", "value": 44})
        spawned = await actions.handler("rsi")(
            ToolCall(
                "spawn_researcher",
                {
                    "direction": "复用观察",
                    "instruction": f"根据[观察]({ref.uri.removesuffix('.json')})继续研究。",
                    "references": [""],
                },
                "spawn",
            )
        )
        agent = spawned.output["agent_id"]
        assert actions.read_record(agent, ref.uri)["value"] == 42
        assert actions.read_record(agent, ref.uri.removesuffix(".json"))["value"] == 42
        assert c.actor(agent).iterations[-1].references[0].uri == ref.uri
        result = await actions.handler("rsi")(
            ToolCall(
                "message_researcher",
                {
                    "agent_id": agent,
                    "instruction": "补充原始证据",
                    "references": ["  ", extra.uri.removesuffix(".json")],
                },
                "update",
            )
        )
        assert "message_id" in result.output
        recovered = ResearchActions(Coordinator(c.store))
        assert recovered.read_record(agent, extra.uri)["value"] == 43
        with pytest.raises(ValueError, match="not shared"):
            recovered.read_record(agent, private.uri.removesuffix(".json"))
        with pytest.raises(ValueError, match="outside"):
            recovered.read_record(agent, ref.uri.replace(c.store.run_id, "another_run"))
        with pytest.raises(ValueError, match="outside"):
            recovered.read_record(agent, f"run://{c.store.run_id}/coordination/config.json")

    asyncio.run(scenario())


def test_targeted_evidence_handoff_includes_sources_without_global_publication(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        c = Coordinator.create(tmp_path, TaskBrief("复用证据", "", ResearchLimits(max_agents=2)))
        c.start()
        source = c.store.put("materials", "source", {"agent_id": "rsi", "value": 42})
        inner = c.store.put(
            "evidence",
            "inner",
            EvidenceRecord("inner", "rsi", "原始观察", "观察结果", now(), references=(source,)),
        )
        outer = c.store.put(
            "evidence",
            "outer",
            EvidenceRecord("outer", "rsi", "研究结论", "结论依据", now(), references=(inner,)),
        )
        actions = ResearchActions(c)
        sent = await actions.handler("rsi")(
            ToolCall(
                "spawn_researcher",
                {"direction": "核对结论", "instruction": "核对原始观察", "references": [outer.uri]},
                "recipient",
            )
        )
        agent = sent.output["agent_id"]
        other = c.spawn(key="other", direction="另一方向", instruction="独立研究")["agent_id"]
        recovered = ResearchActions(Coordinator(c.store))
        assert recovered.read_record(agent, source.uri)["value"] == 42
        assert recovered.read_record(agent, inner.uri)["summary"] == "观察结果"
        assert {e.evidence_id for e in recovered.c.store.evidence(agent)} == {"inner", "outer"}
        assert not recovered.c.store.get("evidence", "outer", EvidenceRecord).shared
        for ref in (outer, inner, source):
            with pytest.raises(ValueError, match="not shared"):
                recovered.read_record(other, ref.uri)

    asyncio.run(scenario())


@pytest.mark.parametrize("reference", ["feedback_exact", "materials/feedback_exact"])
def test_short_reference_handoff_preserves_permissions_and_canonical_grants(
    tmp_path: Path, reference: str
) -> None:
    async def scenario() -> None:
        c = Coordinator.create(
            tmp_path, TaskBrief("复用已有记录", "", ResearchLimits(max_agents=2))
        )
        c.start()
        source, recipient = [
            c.spawn(key=key, direction=key, instruction="完成一个研究阶段")["agent_id"]
            for key in ("source", "recipient")
        ]
        ref = c.store.put("materials", "feedback_exact", {"agent_id": source, "value": 42})
        actions = ResearchActions(c)
        with pytest.raises(ValueError, match="not shared"):
            actions.read_record(recipient, reference)
        denied = await actions.handler(recipient)(
            ToolCall(
                "send_update",
                {"message": "未经授权的引用", "references": [reference]},
                "denied",
            )
        )
        assert "not shared" in denied.output["error"]
        sent = await actions.handler(source)(
            ToolCall(
                "share_update",
                {
                    "agent_ids": [recipient],
                    "message": "请复用原始观察",
                    "references": [reference, "feedback_exact", ref.uri],
                    "save_current": False,
                },
                "handoff",
            )
        )
        assert "error" not in sent.output
        recovered = ResearchActions(Coordinator(c.store))
        assert recovered.read_record(recipient, reference)["value"] == 42
        grants = recovered.c.actor(recipient).iterations[-1].references
        assert [value.uri for value in grants].count(ref.uri) == 1
        message = c.store.get("messages", sent.output["message_id"], ResearchMessage)
        assert message.references == (ref,)

    asyncio.run(scenario())


def test_import_preserves_binary_products_and_does_not_overwrite_work(tmp_path: Path) -> None:
    async def scenario() -> None:
        c = Coordinator.create(tmp_path, TaskBrief("组合数据产物", ""))
        c.start()
        source = tmp_path / "source"
        source.mkdir()
        body = b"\x00\xff\x01\xfe"
        (source / "data.bin").write_bytes(body)
        candidate = await c.store.snapshot(source, agent_id="rsi", description="数据")
        actions = ResearchActions(c)
        agent = c.spawn(key="combine", direction="组合产物", instruction="复用数据")["agent_id"]
        handler = actions.handler(agent)
        args = {"candidate_id": candidate.candidate_id, "destination": "imports/data"}
        result = await handler(ToolCall("import_candidate", args, "copy"))
        target = Path(result.output["directory"])
        assert (target / "data.bin").read_bytes() == body
        assert any(
            candidate.candidate_id in ref.uri for ref in c.actor(agent).iterations[-1].references
        )
        replay = await handler(ToolCall("import_candidate", args, "copy-again"))
        assert replay.output == result.output
        (target / "data.bin").write_bytes(b"local edits")
        refused = await handler(ToolCall("import_candidate", args, "do-not-overwrite"))
        assert "error" in refused.output
        assert (target / "data.bin").read_bytes() == b"local edits"
        escape = await handler(
            ToolCall("import_candidate", {**args, "destination": "../escape"}, "escape")
        )
        assert "error" in escape.output
        assert (source / "data.bin").read_bytes() == body

    asyncio.run(scenario())


def test_progress_wakes_coordinator_without_ending_research_stage(tmp_path: Path) -> None:
    async def scenario() -> None:
        c = Coordinator.create(tmp_path, TaskBrief("研究迭代方法", ""))
        c.start()
        c.begin_turn("rsi")
        spawned = c.spawn(key="branch", direction="一个方向", instruction="研究假设")
        agent = spawned["agent_id"]
        c.end_turn("rsi")
        c.wait("rsi", "等待新的研究证据")
        c.begin_turn(agent)
        node_id = c.actor(agent).current_iteration_id
        actions = ResearchActions(c)
        tool = ToolCall(
            "send_update",
            {"message": "已排除原假设，正在验证另一种解释", "references": []},
            "update",
        )
        sent = await actions.handler(agent)(tool)
        assert not sent.stop and sent.output["stage_continues"]
        assert "rsi" in c.pending()
        assert c.actor(agent).current_iteration_id == node_id
        assert c.actor(agent).iterations[-1].report_ref is None
        recovered = ResearchActions(Coordinator(c.store))
        assert (await recovered.handler(agent)(tool)).output == sent.output
        assert c.state["inboxes"]["rsi"].count(sent.output["message_id"]) == 1
        c.report(agent, key="done", fields={"message": "阶段已完成"})
        c.end_turn(agent)
        assert c.actor(agent).wait_reason == "rsi_reply"
        assert agent not in c.pending()
        after = await actions.handler(agent)(
            ToolCall("send_update", tool.arguments, "after-report")
        )
        assert "error" in after.output

    asyncio.run(scenario())


def test_peer_handoff_is_scoped_and_preserves_report_boundaries(tmp_path: Path) -> None:
    async def scenario() -> None:
        c = Coordinator.create(tmp_path, TaskBrief("组件研究", "", ResearchLimits(max_agents=4)))
        c.start()
        source, recipient, reported, stopped = [
            c.spawn(key=key, direction=key, instruction="推进当前阶段")["agent_id"]
            for key in ("source", "recipient", "reported", "stopped")
        ]
        for agent in (source, recipient, reported, stopped):
            c.begin_turn(agent)
        c.report(reported, key="reported-done", fields={"message": "等待新的阶段"})
        c.end_turn(reported)
        c.stop_agent(stopped, "方向已结束")
        c.wait(recipient, "job:pending")
        c.end_turn(recipient)
        original_node = c.actor(recipient).current_iteration_id
        actions = ResearchActions(c)
        ref = c.store.put("materials", "component", {"agent_id": source, "value": "usable"})
        private = c.store.put("materials", "private", {"agent_id": "rsi", "value": "private"})
        handler = actions.handler(source)
        tool = ToolCall(
            "share_update",
            {
                "agent_ids": [recipient, recipient, reported, stopped],
                "message": f"可复用组件已验证，见 {ref.uri.removesuffix('.json')}。",
                "references": [],
            },
            "share",
        )
        sent = await handler(tool)
        assert sent.output["stage_continues"] and not sent.stop
        assert [d["status"] for d in sent.output["deliveries"]] == [
            "sent",
            "not_active",
            "not_active",
        ]
        peer_id = sent.output["deliveries"][0]["message_id"]
        peer = c.store.get("messages", peer_id, ResearchMessage)
        assert peer.sender == source and peer.recipient == recipient
        assert peer.references == (ref,)
        controller = c.store.get("messages", sent.output["message_id"], ResearchMessage)
        assert controller.sender == source and controller.recipient == "rsi"
        assert c.actor(recipient).current_iteration_id == original_node
        assert c.actor(source).iterations[-1].report_ref is None
        assert (
            recipient in c.pending() and reported not in c.pending() and stopped not in c.pending()
        )
        assert c.actor(reported).wait_reason == "rsi_reply"
        recovered = ResearchActions(Coordinator(c.store))
        assert recovered.read_record(recipient, ref.uri)["value"] == "usable"
        assert (await recovered.handler(source)(tool)).output == sent.output
        assert c.state["inboxes"][recipient].count(peer_id) == 1
        assert source in c.turn_update_senders(recipient)
        c.begin_turn(recipient)
        # An update is the continuation of this node, never a new assignment.
        assert c.actor(recipient).current_iteration_id == original_node
        before = len(list((c.store.root / "messages").glob("*.json")))
        denied = await handler(
            ToolCall("share_update", {**tool.arguments, "references": [private.uri]}, "denied")
        )
        assert "error" in denied.output
        invalid = await handler(
            ToolCall("share_update", {**tool.arguments, "agent_ids": ["foreign-agent"]}, "invalid")
        )
        assert "error" in invalid.output
        assert len(list((c.store.root / "messages").glob("*.json"))) == before
        assert "error" in (await actions.handler("rsi")(tool)).output

    asyncio.run(scenario())


def test_peer_handoff_recovers_after_delivery_and_recipient_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        c = Coordinator.create(tmp_path, TaskBrief("共享恢复", "", ResearchLimits(max_agents=2)))
        c.start()
        source, recipient = [
            c.spawn(key=key, direction=key, instruction="推进当前阶段")["agent_id"]
            for key in ("source", "recipient")
        ]
        for agent in (source, recipient):
            c.begin_turn(agent)
        c.wait(recipient, "等待组件")
        c.end_turn(recipient)
        enqueue = c.enqueue

        def crash_after_copy(sender: str, target: str, text: str, **kwargs: Any) -> str:
            message_id = enqueue(sender, target, text, **kwargs)
            if sender == source and target == "rsi":
                raise RuntimeError("process lost after durable delivery")
            return message_id

        monkeypatch.setattr(c, "enqueue", crash_after_copy)
        call = ToolCall(
            "share_update",
            {"agent_ids": [recipient], "message": "组件已验证", "references": []},
            "partially-committed",
        )
        with pytest.raises(RuntimeError, match="process lost"):
            await ResearchActions(c).handler(source)(call)
        monkeypatch.setattr(c, "enqueue", enqueue)
        peer, _ = c.begin_turn(recipient)
        assert peer.sender == source
        c.report(recipient, key="completed", fields={"message": "已使用共享组件，阶段完成"})
        c.end_turn(recipient)
        messages_before = set((c.store.root / "messages").glob("*.json"))
        recovered = Coordinator(c.store)
        result = await ResearchActions(recovered).handler(source)(call)
        assert "error" not in result.output
        assert result.output["deliveries"] == [{"agent_id": recipient, "status": "not_active"}]
        assert set((c.store.root / "messages").glob("*.json")) == messages_before
        assert recipient not in recovered.pending()
        assert recovered.actor(recipient).wait_reason == "rsi_reply"
        assert recovered.state["inboxes"]["rsi"].count(result.output["message_id"]) == 1

    asyncio.run(scenario())


def test_save_and_share_recovers_the_original_bytes_after_delivery_interruption(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        c = Coordinator.create(
            tmp_path, TaskBrief("组合数据产物", "", ResearchLimits(max_agents=2))
        )
        c.start()
        source, recipient = [
            c.spawn(key=key, direction=key, instruction="研究当前阶段")["agent_id"]
            for key in ("source", "recipient")
        ]
        actions = ResearchActions(c)
        work = actions.workspace(source) / "data.bin"
        work.write_bytes(b"\x00\xfforiginal")
        args = {
            "agent_ids": [recipient],
            "message": "data.bin 中的局部数据已验证，整体仍未通过；请组合实验。",
            "references": [],
            "save_current": True,
        }
        # Invalid contacts must not publish even a snapshot.
        denied = await actions.handler(source)(
            ToolCall("share_update", {**args, "agent_ids": ["foreign"]}, "invalid")
        )
        assert "error" in denied.output
        assert not list((c.store.root / "candidates").glob("*.json"))
        enqueue = c.enqueue

        def interrupt(sender: str, target: str, text: str, **kwargs: Any) -> str:
            if target == recipient:
                raise RuntimeError("interrupted after snapshot")
            return enqueue(sender, target, text, **kwargs)

        monkeypatch.setattr(c, "enqueue", interrupt)
        call = ToolCall("share_update", args, "snapshot-and-share")
        with pytest.raises(RuntimeError, match="after snapshot"):
            await actions.handler(source)(call)
        snapshots = list((c.store.root / "candidates").glob("*.json"))
        assert len(snapshots) == 1
        work.write_bytes(b"later edits")
        recovered = ResearchActions(Coordinator(c.store))
        sent = await recovered.handler(source)(call)
        saved = sent.output["saved_candidate"]
        assert saved["candidate_id"] == snapshots[0].stem
        await c.store.export(saved["candidate_id"], tmp_path / "exported")
        assert (tmp_path / "exported/data.bin").read_bytes() == b"\x00\xfforiginal"
        assert work.read_bytes() == b"later edits"
        peer = c.store.get("messages", sent.output["deliveries"][0]["message_id"], ResearchMessage)
        assert saved["uri"] in [ref.uri for ref in peer.references]
        assert "保存不代表验证通过" in peer.text
        assert c.store.run().best_result_ref is None
        assert (await recovered.handler(source)(call)).output == sent.output
        assert len(list((c.store.root / "candidates").glob("*.json"))) == 1

    asyncio.run(scenario())


def test_snapshot_handoff_does_not_wake_a_recipient_that_reports_while_saving(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        c = Coordinator.create(tmp_path, TaskBrief("协作保存", "", ResearchLimits(max_agents=2)))
        c.start()
        source, recipient = [
            c.spawn(key=key, direction=key, instruction="研究当前阶段")["agent_id"]
            for key in ("source", "recipient")
        ]
        c.begin_turn(recipient)
        actions = ResearchActions(c)
        (actions.workspace(source) / "component.txt").write_text("component")
        snapshot = c.store.snapshot

        async def save_then_report(*args: Any, **kwargs: Any) -> Any:
            result = await snapshot(*args, **kwargs)
            c.report(recipient, key="done", fields={"message": "本阶段完成"})
            c.end_turn(recipient)
            return result

        monkeypatch.setattr(c.store, "snapshot", save_then_report)
        sent = await actions.handler(source)(
            ToolCall(
                "share_update",
                {
                    "agent_ids": [recipient],
                    "message": "当前组件可复用",
                    "references": [],
                    "save_current": True,
                },
                "save-and-share",
            )
        )
        assert sent.output["deliveries"] == [{"agent_id": recipient, "status": "not_active"}]
        assert recipient not in c.pending()
        assert c.actor(recipient).wait_reason == "rsi_reply"
        controller = c.store.get("messages", sent.output["message_id"], ResearchMessage)
        assert sent.output["saved_candidate"]["uri"] in [ref.uri for ref in controller.references]

    asyncio.run(scenario())
