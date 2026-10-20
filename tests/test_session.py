"""Behavioral checks for context transitions, durable controls and execution failures."""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fake_codex import FakeCodex

from areal_rsi.api.control import LessonDraft
from areal_rsi.api.research import ExperienceRef, SessionConfig, SessionPhase, SessionStatus
from areal_rsi.infra.codex import NativeEvent
from areal_rsi.infra.session import SessionSystem


async def eventually(condition: Callable[[], bool]) -> None:
    async with asyncio.timeout(5):
        while not condition():
            await asyncio.sleep(0.01)


class SessionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        FakeCodex.instances.clear()
        FakeCodex.threads.clear()
        self.patcher = patch("areal_rsi.infra.coordination.runtime.CodexClient", FakeCodex)
        self.patcher.start()
        self.system = SessionSystem(self.root / "data")
        self.run_id = await self.system.create(SessionConfig(str(self.root), auto_learn=False))

    async def asyncTearDown(self) -> None:
        await self.system.close()
        self.patcher.stop()
        self.temporary.cleanup()

    @property
    def client(self) -> FakeCodex:
        return FakeCodex.instances[-1]

    async def active(self, previous: str = "") -> None:
        await eventually(
            lambda: (
                bool(self.system.detail(self.run_id).active_turn_id)
                and self.system.detail(self.run_id).active_turn_id != previous
            )
        )

    async def leader(self, *, token_budget: int | None = None) -> None:
        await self.system.enter_rsi(self.run_id, "Prepare and start")
        await self.active()
        response = await self.client.tool(
            1,
            "rsi_set_task",
            {
                "objective": "Improve the interface",
                "instructions": "Keep the data semantics",
                "acceptance": ["Check rendered output"],
            },
        )
        self.assertTrue(response["success"])
        if token_budget is not None:
            await self.system.set_budget(self.run_id, max_total_tokens=token_budget)
        self.assertTrue((await self.client.tool(2, "rsi_start_goal", {}))["success"])
        await self.client.finish()
        await eventually(lambda: self.system.detail(self.run_id).phase == SessionPhase.LEADER)
        await self.active()

    async def test_model_switch_preserves_active_turn_and_survives_reopen(self) -> None:
        await self.system.send_message(self.run_id, "Inspect the current project")
        await self.active()
        before = self.system.detail(self.run_id)
        requests = len(self.client.requests)
        with self.assertRaises(ValueError):
            await self.system.set_model(self.run_id, "   ")
        with self.assertRaises(ValueError):
            await self.system.set_effort(self.run_id, "invalid")
        self.assertEqual(self.system.detail(self.run_id).config.model, before.config.model)
        self.assertEqual(self.system.detail(self.run_id).config.effort, before.config.effort)
        await self.system.set_model(self.run_id, "  gpt-6-sol  ")
        await self.system.set_effort(self.run_id, "low")
        self.assertEqual(len(self.client.requests), requests)
        self.assertEqual(self.system.detail(self.run_id).active_turn_id, before.active_turn_id)
        await self.client.finish()
        await eventually(lambda: self.system.detail(self.run_id).status == SessionStatus.IDLE)
        await self.system.send_message(self.run_id, "Continue with the same context")
        await self.active()
        starts = [params for method, params in self.client.requests if method == "turn/start"]
        self.assertEqual([params["model"] for params in starts], [before.config.model, "gpt-6-sol"])
        self.assertEqual([params["effort"] for params in starts], [before.config.effort, "low"])
        self.assertTrue(all(params["threadId"] == before.thread_id for params in starts))
        await self.system.pause(self.run_id)
        await self.system.close()
        self.system = SessionSystem(self.root / "data")
        record = await self.system.open(self.run_id)
        self.assertEqual(record.config.model, "gpt-6-sol")
        self.assertEqual(record.config.effort, "low")
        self.assertEqual(record.status, SessionStatus.PAUSED)
        self.assertFalse(record.active_turn_id)

    async def test_rsi_waits_for_active_work_and_keeps_thread(self) -> None:
        await self.system.send_message(self.run_id, "Inspect business data")
        await self.active()
        source = self.system.detail(self.run_id).thread_id
        await self.system.send_message(self.run_id, "Also inspect empty values")
        self.assertEqual(self.client.requests[-1][0], "turn/steer")
        await self.system.enter_rsi(self.run_id, "Discuss the redesign")
        self.assertEqual(self.system.detail(self.run_id).phase, SessionPhase.CHAT)
        await self.client.finish()
        await eventually(lambda: self.system.detail(self.run_id).phase == SessionPhase.INTERACTION)
        await self.active()
        self.assertEqual(self.system.detail(self.run_id).thread_id, source)
        self.assertFalse(any(method == "thread/fork" for method, _ in self.client.requests))
        params = next(
            params for method, params in reversed(self.client.requests) if method == "turn/start"
        )
        self.assertIn("此前会话", params["additionalContext"]["rsi-role"]["value"])

    async def test_role_checks_receipts_and_completion_evidence(self) -> None:
        await self.system.send_message(self.run_id, "Ordinary task")
        await self.active()
        rejected = await self.client.tool(
            10,
            "rsi_update_goal",
            {
                "status": "completed",
                "summary": "Fake success",
                "evidence": ["claim"],
            },
        )
        self.assertFalse(rejected["success"])
        await self.client.finish()
        await eventually(lambda: self.system.detail(self.run_id).status == SessionStatus.IDLE)
        await self.leader()
        missing = await self.client.tool(
            11,
            "rsi_update_goal",
            {
                "status": "completed",
                "summary": "No evidence",
            },
        )
        self.assertFalse(missing["success"])
        arguments = {"status": "completed", "summary": "Checked", "evidence": ["Tests passed"]}
        first = await self.client.tool(12, "rsi_update_goal", arguments, call_id="stable")
        replay = await self.client.tool(13, "rsi_update_goal", arguments, call_id="stable")
        self.assertEqual(first, replay)
        forged = await self.client.tool(
            14,
            "rsi_update_goal",
            {
                **arguments,
                "summary": "changed",
            },
            call_id="stable",
        )
        self.assertFalse(forged["success"])
        self.assertEqual(self.system.detail(self.run_id).status, SessionStatus.RUNNING)
        await self.client.finish()
        await eventually(lambda: self.system.detail(self.run_id).status == SessionStatus.COMPLETED)
        saved = self.system.store.load(self.run_id)
        self.assertEqual(saved.progress.summary, "Checked")
        self.assertEqual(len(saved.receipts), 3)

    async def test_empty_continuations_wait_instead_of_spinning(self) -> None:
        await self.leader()
        for index in range(3):
            turn = self.system.detail(self.run_id).active_turn_id
            await self.client.finish("Thinking without executing")
            if index < 2:
                await self.active(previous=turn)
        await eventually(lambda: self.system.detail(self.run_id).status == SessionStatus.WAITING)
        count = len(self.client.requests)
        await asyncio.sleep(0.3)
        self.assertEqual(count, len(self.client.requests))
        await self.system.send_message(self.run_id, "Use the provided screenshot")
        await self.active()

    async def test_user_message_racing_completion_is_not_stranded(self) -> None:
        await self.leader()
        await self.client.tool(
            99,
            "rsi_update_goal",
            {"status": "completed", "summary": "Done", "evidence": ["Checked output"]},
        )
        turn = FakeCodex.threads[self.client.thread_id].turns[-1]
        turn["status"] = "completed"
        self.client.turn_id = ""  # Native turn ended; host has not received its notification yet.
        await self.system.send_message(self.run_id, "Also preserve the nullable relationship")
        await self.client.events.put(
            NativeEvent("turn/completed", {"threadId": self.client.thread_id, "turn": turn})
        )
        await self.active(previous=turn["id"])
        starts = [params for method, params in self.client.requests if method == "turn/start"]
        self.assertEqual(starts[-1]["input"][0]["text"], "Also preserve the nullable relationship")
        self.assertEqual(self.system.detail(self.run_id).progress.decision, "active")

    async def test_sleep_assertion_tracks_work_and_user_waits(self) -> None:
        await self.leader()
        self.assertTrue(self.client.awake)
        turn = self.system.detail(self.run_id).active_turn_id
        await self.client.finish()
        await self.active(previous=turn)
        self.assertTrue(self.client.awake)
        await self.system.pause(self.run_id)
        self.assertFalse(self.client.awake)
        await eventually(lambda: not self.system.detail(self.run_id).active_turn_id)
        await self.system.resume(self.run_id)
        await self.active()
        self.assertTrue(self.client.awake)
        await self.system.close()
        self.assertFalse(self.client.awake)

    async def test_automatic_learning_failure_preserves_completed_goal_and_can_retry(self) -> None:
        self.run_id = await self.system.create(
            replace(self.system.detail(self.run_id).config, auto_learn=True)
        )
        await self.leader()
        summarize = AsyncMock(side_effect=RuntimeError("curator disconnected"))
        with patch.object(self.system.experience, "consolidate", summarize):
            await self.client.tool(
                99,
                "rsi_update_goal",
                {"status": "completed", "summary": "Done", "evidence": ["Checked artifact"]},
            )
            await self.client.finish()
            await eventually(lambda: self.system.detail(self.run_id).learning.status == "failed")
            self.assertEqual(self.system.detail(self.run_id).status, SessionStatus.COMPLETED)
            document = self.system.experience.publish(
                LessonDraft("validation", "Validation", "When changing behavior", "Check results"),
            )
            summarize.side_effect = None
            summarize.return_value = document
            self.assertEqual(await self.system.learn(self.run_id), document)
            self.assertEqual(await self.system.learn(self.run_id), document)
            self.assertEqual(summarize.await_count, 2, "Duplicate trigger should reuse the result")

    async def test_guided_learning_waits_across_reopen_and_uses_human_feedback(self) -> None:
        await self.system.set_learning_mode(self.run_id, "guided")
        with self.assertRaisesRegex(ValueError, "auto.*guided"):
            await self.system.set_learning_mode(self.run_id, "invalid")
        await self.leader()
        document = self.system.experience.publish(
            LessonDraft("validation", "Validation", "After edits", "Check results"),
        )
        summarize = AsyncMock(return_value=document)
        with patch.object(self.system.experience, "consolidate", summarize):
            await self.client.tool(
                99,
                "rsi_update_goal",
                {"status": "completed", "summary": "Done", "evidence": ["Checked output"]},
            )
            await self.client.finish()
            await eventually(
                lambda: self.system.detail(self.run_id).learning.status == "awaiting_feedback"
            )
            self.assertEqual(self.system.detail(self.run_id).status, SessionStatus.COMPLETED)
            self.assertFalse(self.client.awake)
            summarize.assert_not_awaited()
        await self.system.close()
        self.system = SessionSystem(self.root / "data")
        reopened = await self.system.open(self.run_id)
        self.assertFalse(reopened.config.auto_learn)
        self.assertEqual(reopened.learning.status, "awaiting_feedback")
        self.assertFalse(self.client.awake)
        feedback = "The implementation worked, but test edge cases before visual polish."
        with patch.object(self.system.experience, "consolidate", summarize):
            self.assertEqual(await self.system.learn(self.run_id, feedback), document)
        assert summarize.await_args is not None
        snapshot, root, actual_feedback = summarize.await_args.args
        self.assertEqual(snapshot.last_turn_id, reopened.last_turn_id)
        self.assertEqual(snapshot.thread_id, reopened.thread_id)
        self.assertEqual(root, self.system.root)
        self.assertEqual(actual_feedback, feedback)
        self.assertEqual(self.system.detail(self.run_id).learning.status, "completed")
        self.assertEqual(self.system.detail(self.run_id).status, SessionStatus.COMPLETED)

        # A follow-up fix must retain the last document through both waiting and curation.
        await self.system.send_message(self.run_id, "Fix a rendering regression")
        await self.active()
        await self.client.tool(
            100,
            "rsi_update_goal",
            {"status": "completed", "summary": "Fixed", "evidence": ["Checked updated output"]},
        )
        await self.client.finish()
        await eventually(
            lambda: self.system.detail(self.run_id).learning.status == "awaiting_feedback"
        )
        self.assertEqual(self.system.detail(self.run_id).learning.document, document)
        summarize.reset_mock()
        with patch.object(self.system.experience, "consolidate", summarize):
            await self.system.learn(self.run_id, "Merge the new regression lesson")
        assert summarize.await_args is not None
        self.assertEqual(summarize.await_args.args[0].learning.document, document)

    async def test_switch_from_guided_to_auto_starts_pending_summary(self) -> None:
        await self.leader()
        await self.client.tool(
            99,
            "rsi_update_goal",
            {"status": "completed", "summary": "Done", "evidence": ["Checked output"]},
        )
        await self.client.finish()
        await eventually(
            lambda: self.system.detail(self.run_id).learning.status == "awaiting_feedback"
        )
        document = self.system.experience.publish(
            LessonDraft("validation", "Validation", "After edits", "Check results"),
        )
        summarize = AsyncMock(return_value=document)
        with patch.object(self.system.experience, "consolidate", summarize):
            await self.system.set_learning_mode(self.run_id, "auto")
            await self.system.learn(self.run_id)
            self.assertEqual(summarize.await_count, 1)
        self.assertTrue(self.system.store.load(self.run_id).config.auto_learn)
        self.assertEqual(self.system.detail(self.run_id).learning.status, "completed")

    async def test_existing_run_refreshes_experience_without_adding_interaction_requirements(
        self,
    ) -> None:
        self.assertFalse(self.system.detail(self.run_id).experience)
        document = self.system.experience.publish(
            LessonDraft("validation", "Validation", "After edits", "Check results"),
        )
        await self.leader()
        starts = [params for method, params in self.client.requests if method == "turn/start"]
        interaction = starts[0]["additionalContext"]["rsi-experience"]["value"]
        leader = starts[-1]["additionalContext"]["rsi-experience"]["value"]
        self.assertNotIn(document.path, interaction)
        self.assertIn(document.path, leader)

    async def test_automatic_curation_keeps_the_host_awake_until_publication(self) -> None:
        self.run_id = await self.system.create(
            replace(self.system.detail(self.run_id).config, auto_learn=True)
        )
        await self.leader()
        document = self.system.experience.publish(
            LessonDraft("validation", "Validation", "After edits", "Check results"),
        )
        release = asyncio.Event()

        async def summarize(*args: object) -> ExperienceRef:
            await release.wait()
            return document

        with patch.object(self.system.experience, "consolidate", summarize):
            await self.client.tool(
                99,
                "rsi_update_goal",
                {"status": "completed", "summary": "Done", "evidence": ["Checked output"]},
            )
            await self.client.finish()
            await eventually(lambda: self.system.detail(self.run_id).learning.status == "running")
            self.assertEqual(self.system.detail(self.run_id).status, SessionStatus.COMPLETED)
            self.assertTrue(self.client.awake)
            with self.assertRaisesRegex(ValueError, "正在总结"):
                await self.system.learn(self.run_id, "An independent check found a regression")
            with self.assertRaisesRegex(ValueError, "正在总结"):
                await self.system.set_learning_mode(self.run_id, "guided")
            release.set()
            await self.system.learn(self.run_id)
            self.assertFalse(self.client.awake)

    async def test_budget_interrupts_and_chat_retains_history(self) -> None:
        await self.leader(token_budget=30)
        thread_id = self.system.detail(self.run_id).thread_id
        await self.client.events.put(
            NativeEvent(
                "thread/tokenUsage/updated",
                {
                    "threadId": thread_id,
                    "tokenUsage": {"total": {"totalTokens": 30}},
                },
            )
        )
        await eventually(
            lambda: self.system.detail(self.run_id).status == SessionStatus.BUDGET_LIMITED
        )
        await eventually(lambda: not self.system.detail(self.run_id).active_turn_id)
        with self.assertRaisesRegex(ValueError, "预算"):
            await self.system.resume(self.run_id)
        await self.system.chat(self.run_id)
        await eventually(lambda: self.system.detail(self.run_id).phase == SessionPhase.CHAT)
        await self.system.send_message(self.run_id, "Review the work")
        await self.active()
        self.assertEqual(self.system.detail(self.run_id).thread_id, thread_id)

    async def test_interaction_cannot_set_or_overwrite_user_budgets(self) -> None:
        await self.system.enter_rsi(self.run_id, "Prepare the task")
        await self.active()
        task = {"objective": "Build a game", "instructions": "Work locally", "acceptance": ["Play"]}
        rejected = await self.client.tool(20, "rsi_set_task", {**task, "max_total_tokens": 12000})
        self.assertFalse(rejected["success"])
        self.assertIsNone(self.system.detail(self.run_id).goal)
        self.assertTrue((await self.client.tool(21, "rsi_set_task", task))["success"])
        goal = self.system.detail(self.run_id).goal
        assert goal is not None
        self.assertIsNone(goal.limits.max_total_tokens)
        self.assertIsNone(goal.limits.max_wall_seconds)
        await self.system.set_budget(self.run_id, max_total_tokens=50000, max_wall_seconds=600)
        self.assertTrue((await self.client.tool(22, "rsi_set_task", task))["success"])
        goal = self.system.detail(self.run_id).goal
        assert goal is not None
        self.assertEqual(goal.limits.max_total_tokens, 50000)
        self.assertEqual(goal.limits.max_wall_seconds, 600)

    async def test_default_unlimited_goal_continues_past_previous_caps(self) -> None:
        await self.leader()
        before = self.system.detail(self.run_id)
        self.system.live[self.run_id].record.seconds_used = 36000
        await self.client.events.put(
            NativeEvent(
                "thread/tokenUsage/updated",
                {
                    "threadId": before.thread_id,
                    "tokenUsage": {"total": {"totalTokens": 1000000}},
                },
            )
        )
        await eventually(lambda: self.system.detail(self.run_id).tokens_used == 1000000)
        await self.client.finish()
        await self.active(before.active_turn_id)
        self.assertEqual(self.system.detail(self.run_id).status, SessionStatus.RUNNING)
        self.assertFalse(any(method == "turn/interrupt" for method, _ in self.client.requests))

    async def test_clear_exhausted_budget_survives_reopen_and_resumes_same_goal(self) -> None:
        await self.leader()
        await self.system.set_budget(self.run_id, max_wall_seconds=0.01)
        await eventually(
            lambda: self.system.detail(self.run_id).status == SessionStatus.BUDGET_LIMITED
        )
        await eventually(lambda: not self.system.detail(self.run_id).active_turn_id)
        before = self.system.detail(self.run_id)
        with self.assertRaisesRegex(ValueError, "positive"):
            await self.system.set_budget(self.run_id, max_total_tokens=0)
        self.assertEqual(self.system.detail(self.run_id).goal, before.goal)
        await self.system.close()
        self.system = SessionSystem(self.root / "data")
        await self.system.open(self.run_id)
        with self.assertRaisesRegex(ValueError, "/budget off"):
            await self.system.resume(self.run_id)
        await self.system.set_budget(self.run_id)
        cleared = self.system.detail(self.run_id)
        self.assertEqual(cleared.status, SessionStatus.PAUSED)
        self.assertEqual(cleared.seconds_used, before.seconds_used)
        self.assertEqual(cleared.tokens_used, before.tokens_used)
        self.assertEqual(cleared.thread_id, before.thread_id)
        self.assertEqual(cleared.in_flight, before.in_flight)
        assert cleared.goal is not None and before.goal is not None
        self.assertEqual(cleared.goal.objective, before.goal.objective)
        self.assertIsNone(cleared.goal.limits.max_total_tokens)
        self.assertIsNone(cleared.goal.limits.max_wall_seconds)
        self.assertEqual(self.system.store.load(self.run_id).goal, cleared.goal)
        self.assertFalse(any(method == "turn/start" for method, _ in self.client.requests))
        await self.system.resume(self.run_id)
        await self.active()
        self.assertEqual(self.system.detail(self.run_id).thread_id, before.thread_id)
        self.assertFalse(any(method == "thread/fork" for method, _ in self.client.requests))
        request = next(params for method, params in self.client.requests if method == "turn/start")
        self.assertIn("核对", request["input"][0]["text"])

    async def test_approval_and_questions_require_explicit_answers(self) -> None:
        await self.system.send_message(self.run_id, "Do work")
        await self.active()
        await self.client.events.put(
            NativeEvent(
                "item/commandExecution/requestApproval",
                {
                    "threadId": self.client.thread_id,
                    "command": "deploy example",
                },
                70,
            )
        )
        await eventually(lambda: bool(self.system.prompts(self.run_id)))
        self.assertNotIn(70, self.client.responses)
        await self.system.answer(self.run_id, 70, approved=False)
        self.assertEqual(self.client.responses[70], {"decision": "decline"})
        await self.client.events.put(
            NativeEvent(
                "item/tool/requestUserInput",
                {
                    "threadId": self.client.thread_id,
                    "questions": [{"id": "palette", "question": "Which palette?", "options": []}],
                },
                71,
            )
        )
        await eventually(lambda: bool(self.system.prompts(self.run_id)))
        with self.assertRaises(ValueError):
            await self.system.answer(self.run_id, 71, answers={"wrong": "blue"})
        await self.system.answer(self.run_id, 71, answers={"palette": "blue"})
        self.assertEqual(self.client.responses[71], {"answers": {"palette": {"answers": ["blue"]}}})
        with self.assertRaises(ValueError):
            await self.system.answer(self.run_id, 71, answers={"palette": "red"})

    async def test_disconnect_does_not_replay_unknown_work(self) -> None:
        await self.system.send_message(self.run_id, "Change files")
        await self.active()
        await self.client.events.put(NativeEvent("transport/closed", {"error": "lost connection"}))
        await eventually(lambda: self.system.detail(self.run_id).status == SessionStatus.FAILED)
        await asyncio.sleep(0.3)
        self.assertEqual(sum(method == "turn/start" for method, _ in self.client.requests), 1)
        pending = self.system.store.load(self.run_id).in_flight
        assert pending is not None
        self.assertEqual(pending.text, "Change files")
        output = self.system.live[self.run_id].output
        old_client = self.client
        await self.system.resume(self.run_id)
        await self.active()
        self.assertIsNot(self.client, old_client)
        self.assertTrue(old_client.closed)
        self.assertIs(output, self.system.live[self.run_id].output)
        params = next(params for method, params in self.client.requests if method == "turn/start")
        self.assertIn("不要重复有副作用", params["input"][0]["text"])

    async def test_pause_preserves_a_pending_rsi_transition(self) -> None:
        await self.system.send_message(self.run_id, "Inspect files")
        await self.active()
        await self.system.enter_rsi(self.run_id, "Discuss a goal afterwards")
        await self.system.pause(self.run_id)
        await eventually(lambda: not self.system.detail(self.run_id).active_turn_id)
        await asyncio.sleep(0.3)
        self.assertEqual(self.system.detail(self.run_id).phase, SessionPhase.CHAT)
        self.assertEqual(self.system.detail(self.run_id).status, SessionStatus.PAUSED)
        await self.system.resume(self.run_id)
        await eventually(lambda: self.system.detail(self.run_id).phase == SessionPhase.INTERACTION)
        await self.active()

    async def test_chat_can_leave_a_failed_session(self) -> None:
        await self.system.open(self.run_id)
        self.client.fail_method = "turn/start"
        await self.system.send_message(self.run_id, "Inspect files")
        await eventually(lambda: self.system.detail(self.run_id).status == SessionStatus.FAILED)
        thread_id = self.system.detail(self.run_id).thread_id
        await self.system.chat(self.run_id)
        await eventually(lambda: self.system.detail(self.run_id).status == SessionStatus.IDLE)
        await self.system.send_message(self.run_id, "Try a new task")
        await self.active()
        self.assertEqual(thread_id, self.system.detail(self.run_id).thread_id)

    async def test_failed_start_preserves_delivery_without_automatic_retry(self) -> None:
        await self.system.open(self.run_id)
        self.client.fail_method = "turn/start"
        await self.system.send_message(self.run_id, "Perform a side effect")
        await eventually(lambda: self.system.detail(self.run_id).status == SessionStatus.FAILED)
        await asyncio.sleep(0.3)
        self.assertEqual(sum(method == "turn/start" for method, _ in self.client.requests), 1)
        self.assertIsNotNone(self.system.detail(self.run_id).in_flight)

    async def test_failed_steer_retains_the_users_new_requirement(self) -> None:
        await self.system.send_message(self.run_id, "Implement a page")
        await self.active()
        self.client.fail_method = "turn/steer"
        with self.assertRaisesRegex(RuntimeError, "unknown"):
            await self.system.send_message(self.run_id, "Retain nullable parent_order_id")
        saved = self.system.store.load(self.run_id)
        assert saved.in_flight is not None
        self.assertEqual(saved.in_flight.text, "Retain nullable parent_order_id")
        await self.system.resume(self.run_id)
        await self.active()
        params = next(params for method, params in self.client.requests if method == "turn/start")
        self.assertIn("Retain nullable parent_order_id", params["input"][0]["text"])

    async def test_stalled_interrupt_closes_owned_process_and_retains_pause(self) -> None:
        await self.system.send_message(self.run_id, "Long work")
        await self.active()
        thread_id = self.system.detail(self.run_id).thread_id
        self.client.fail_method = "turn/interrupt"
        await self.system.pause(self.run_id)
        self.assertTrue(self.client.closed)
        saved = self.system.detail(self.run_id)
        self.assertEqual(saved.status, SessionStatus.PAUSED)
        self.assertFalse(saved.active_turn_id)
        self.assertIsNotNone(saved.in_flight)
        await self.system.resume(self.run_id)
        await self.active()
        self.assertEqual(self.system.detail(self.run_id).thread_id, thread_id)

    async def test_run_owner_is_released_after_close(self) -> None:
        await self.system.open(self.run_id)
        other = SessionSystem(self.root / "data")
        try:
            with self.assertRaisesRegex(ValueError, "already open"):
                await other.open(self.run_id)
            thread_id = self.system.detail(self.run_id).thread_id
            await self.system.close()
            restored = await other.open(self.run_id)
            self.assertEqual(restored.thread_id, thread_id)
        finally:
            await other.close()

    async def test_failed_connection_releases_owner_and_closes_client(self) -> None:
        with (
            patch.object(FakeCodex, "start", AsyncMock(side_effect=RuntimeError("cannot connect"))),
            self.assertRaisesRegex(RuntimeError, "cannot connect"),
        ):
            await self.system.open(self.run_id)
        self.assertTrue(self.client.closed)
        self.assertNotIn(self.run_id, self.system.live)
        other = SessionSystem(self.root / "data")
        try:
            restored = await other.open(self.run_id)
            self.assertTrue(restored.thread_id)
            self.assertEqual(restored.status, SessionStatus.IDLE)
        finally:
            await other.close()

    async def test_failed_leader_start_recovers_the_committed_fork(self) -> None:
        await self.system.enter_rsi(self.run_id, "Prepare the task")
        await self.active()
        source = self.client.thread_id
        await self.client.tool(
            1,
            "rsi_set_task",
            {
                "objective": "Improve the interface",
                "instructions": "Keep business semantics",
                "acceptance": ["Check rendered output"],
            },
        )
        await self.system.start(self.run_id)
        self.client.fail_method = "turn/start"
        await self.client.finish()
        await eventually(lambda: self.system.detail(self.run_id).status == SessionStatus.FAILED)
        failed = self.system.store.load(self.run_id)
        self.assertEqual(failed.phase, SessionPhase.LEADER)
        self.assertEqual(failed.source_thread_id, source)
        self.assertNotEqual(failed.thread_id, source)
        self.assertIsNotNone(failed.in_flight)
        await self.system.resume(self.run_id)
        await self.active()
        self.assertEqual(self.system.detail(self.run_id).thread_id, failed.thread_id)
        self.assertFalse(any(method == "thread/fork" for method, _ in self.client.requests))
        started = next(params for method, params in self.client.requests if method == "turn/start")
        self.assertIn("不要重复有副作用", started["input"][0]["text"])

    async def test_recovery_recognizes_a_completed_turn_whose_notification_was_lost(self) -> None:
        await self.system.send_message(self.run_id, "Finish the change")
        await self.active()
        record = self.system.detail(self.run_id)
        FakeCodex.threads[record.thread_id].turns[-1]["status"] = "completed"
        await self.system.close()
        restored = await self.system.open(self.run_id)
        self.assertEqual(restored.last_turn_id, record.active_turn_id)
        self.assertEqual(restored.status, SessionStatus.IDLE)
        self.assertIsNone(restored.in_flight)
        self.assertFalse(restored.active_turn_id)
        self.assertFalse(any(method == "turn/start" for method, _ in self.client.requests))
