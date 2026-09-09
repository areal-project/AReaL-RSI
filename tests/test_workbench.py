"""HTTP acceptance fixtures. ``python tests/test_workbench.py --serve`` previews mock data."""

from __future__ import annotations

import asyncio
import copy
import http.client
import json
import sys
import tempfile
import threading
import unittest
from datetime import UTC, datetime
from typing import Any

from areal_rsi.ui.workbench import MAX_BODY_BYTES, WorkbenchServer

Json = dict[str, Any]
PRIVATE = "PRIVATE_SENTINEL_DO_NOT_EXPOSE"


class FixtureBackend:
    """Deterministic, explicitly simulated data for browser and HTTP acceptance."""

    def __init__(self, *, include_demo: bool = False):
        self.runs: dict[str, Json] = {}
        self.calls: list[tuple[str, Json]] = []
        self.bootstrap = {
            "allow_simulation": True,
            "default_simulated": True,
            "has_config": False,
            "private": PRIVATE,
        }
        self.temporary = tempfile.TemporaryDirectory()
        if include_demo:
            run = self.create(
                {"objective": "为 KernelBench 优化可复用的 kernel skills", "max_agents": 2}
            )
            self.populate(run)

    def close(self) -> None:
        self.temporary.cleanup()

    def create(self, params: Json) -> Json:
        objective = params.get("objective") or params.get("message") or "新的研究"
        run_id = f"demo_{len(self.runs) + 1}"
        run: Json = {
            "run_id": run_id,
            "objective": objective,
            "status": "planned",
            "simulated": True,
            "plan": "",
            "intake": {
                "status": "discussing",
                "summary": "",
                "environment": "",
                "private": PRIVATE,
            },
            "agents": [],
            "nodes": [],
            "messages": [],
            "jobs": [],
            "events": [],
            "edges": [],
            "event_cursor": 0,
            "baseline": None,
            "best": None,
            "final": None,
            "waiting_for_user": [],
            "budget": {"model_calls": 0, "total_tokens": 0, "verify_calls": 0},
            "brief": {
                "objective": objective,
                "instructions": params.get("instructions", ""),
                "limits": {
                    "max_agents": params.get("max_agents", 1),
                    "max_nodes": 20,
                    "max_model_calls": 200,
                    "max_total_tokens": 1000000,
                    "max_jobs": 1,
                    "gpu_ids": [],
                    "allowed_paths": ["skills/*.md"],
                },
                "verification": {
                    "metric": "fast_1",
                    "direction": "maximize",
                    "target": 0.8,
                    "description": "固定目标模型、任务集合、GPU 和每题预算。此处全部为模拟数据。",
                    "private_labels": PRIVATE,
                },
            },
            "config": {"api_key": PRIVATE},
            "session_raw": PRIVATE,
            "private": PRIVATE,
        }
        self.runs[run_id] = run
        self.event(run, "run_created", title="创建研究空间")
        return run

    def event(self, run: Json, kind: str, **data: Any) -> None:
        run["event_cursor"] += 1
        run["events"].append(
            {
                "sequence": run["event_cursor"],
                "kind": kind,
                "created_at": datetime.now(UTC).isoformat(),
                "data": {**data, "private": PRIVATE},
            }
        )

    def populate(self, run: Json) -> None:
        run.update(
            status="running",
            plan=(
                "先检查性能损失的共同原因，再比较两条可复用的优化方向。"
                "只修改 skills；由独立验证记录决定是否保留候选版本。"
                "当前正在验证内存访问建议，另一方向的反例已共享。"
            ),
        )
        run["agents"] = [
            {"agent_id": "rsi", "state": "waiting", "wait_reason": "等待验证作业完成"},
            {"agent_id": "interaction", "state": "waiting", "wait_reason": "user"},
            {
                "agent_id": "research_a",
                "direction": "内存访问与任务分解",
                "state": "waiting",
                "wait_reason": "job:verify_2",
                "current_iteration_id": "n2",
                "session": PRIVATE,
            },
            {
                "agent_id": "research_b",
                "direction": "数值稳定性与正确性",
                "state": "waiting",
                "wait_reason": "rsi_reply",
                "current_iteration_id": "n3",
            },
        ]
        report = {
            "report_id": "report_a",
            "task_status": "已完成首轮轨迹分析；验证结果为模拟数据。",
            "work": (
                "阅读 6 条开发轨迹，比较失败重试的共同模式；"
                "为 skills 增加内存布局检查和分步验证建议。"
            ),
            "problems": (
                "两条轨迹把转置误当作连续内存。此前建议缺少 stride 检查，"
                "现已补充；真实 GPU 表现尚未验证。"
            ),
            "results": (
                "发现 2 类可复用失败模式，形成 1 份候选 skills。模拟验证为 0.72；"
                "该数字仅用于测试工作台，不代表性能。"
            ),
            "interpretation": "现有证据支持补充前置布局检查，尚不能证明该建议在未见题目上有收益。",
            "next_steps": "验证候选 skills 的通用性，并纳入另一方向观察到的精度反例。",
            "shared_findings": [
                "在选择 kernel 分块前显式检查输入 stride；适用于非连续输入，需保留正确性检查。"
            ],
            "references": [{"uri": f"run://{run['run_id']}/evidence/layout.json"}],
            "raw_trace": PRIVATE,
        }
        run["nodes"] = [
            {
                "iteration_id": "n1",
                "agent_id": "research_a",
                "state": "completed",
                "parent_iteration_id": None,
                "assignment": {
                    "instruction": "提取轨迹中的通用失败模式",
                    "completion_criteria": "说明具体问题、支持证据和可复用建议",
                },
                "report": report,
                "evidence": [
                    {
                        "evidence_id": "layout",
                        "title": "非连续输入中的布局假设",
                        "summary": "两条开发轨迹遗漏 stride 检查，失败原因相同。",
                        "limitations": "开发集中的观察；尚无泛化收益证据。",
                        "shared": True,
                        "private": PRIVATE,
                    }
                ],
                "verification": {"score": 0.72, "simulated": True, "phase": "search"},
            },
            {
                "iteration_id": "n2",
                "agent_id": "research_a",
                "state": "running",
                "parent_iteration_id": "n1",
                "assignment": {
                    "instruction": "验证布局建议并吸收精度反例",
                    "completion_criteria": "对照同一验证协议报告收益与代价",
                },
                "report": None,
                "evidence": [],
                "verification": None,
            },
            {
                "iteration_id": "n3",
                "agent_id": "research_b",
                "state": "completed",
                "parent_iteration_id": None,
                "assignment": {
                    "instruction": "分析精度下降的共同触发条件",
                    "completion_criteria": "保存有证据的反例和适用边界",
                },
                "report": {
                    **report,
                    "report_id": "report_b",
                    "work": "检查 reductions 的精度风险，记录低精度累加导致偏差的反例。",
                    "results": "形成一份反例记录；本节点不产生候选版本或验证分数。",
                },
                "evidence": [],
                "verification": None,
            },
        ]
        run["edges"] = [
            {"source": "n3", "target": "n2", "kind": "merge", "label": "将精度反例纳入下一轮建议"}
        ]
        run["best"] = {
            "candidate_id": "candidate_a",
            "score": 0.72,
            "passed": True,
            "status": "succeeded",
            "simulated": True,
            "phase": "search",
            "private": PRIVATE,
        }
        run["budget"] = {
            "model_calls": 12,
            "total_tokens": 18340,
            "verify_calls": 1,
            "reserved_tokens": 24000,
        }
        run["jobs"] = [
            {
                "job_id": "verify_2",
                "agent_id": "research_a",
                "status": "queued",
                "description": "验证更新后的 kernel skills",
                "progress_message": "等待 GPU 分配 · 当前为模拟作业",
                "private_log": PRIVATE,
            }
        ]
        run["messages"] = [
            {
                "message_id": "m1",
                "sender": "user",
                "recipient": "interaction",
                "text": "只优化 skills，先分析通用问题，不要针对具体题目编写答案。",
                "created_at": datetime.now(UTC).isoformat(),
            },
            {
                "message_id": "m2",
                "sender": "interaction",
                "recipient": "user",
                "text": (
                    "已将可修改范围限定为 skills。RSI 正沿布局检查和数值稳定性两个方向探索，"
                    "当前最好的模拟验证结果为 0.72。真实收益仍需 GPU 验证。"
                ),
                "created_at": datetime.now(UTC).isoformat(),
            },
            {"message_id": "hidden", "sender": "research_a", "recipient": "rsi", "text": PRIVATE},
        ]
        for kind, title in [
            ("run_started", "开始模拟研究"),
            ("agent_created", "建立两个研究方向"),
            ("report_submitted", "收到布局检查报告"),
            ("evidence_shared", "共享精度反例"),
            ("job_submitted", "等待模拟验证"),
        ]:
            self.event(run, kind, title=title)

    async def call(self, action: str, params: Json) -> Json:
        self.calls.append((action, params))
        await asyncio.sleep(0)
        if action == "bootstrap":
            return self.bootstrap
        if action == "list":
            return {"runs": list(reversed(list(self.runs.values())))}
        if action == "create":
            created = self.create(params)
            if params.get("message"):
                await self.call("chat", {"run_id": created["run_id"], "text": params["message"]})
            return {"run_id": created["run_id"], "private": PRIVATE}
        run = self.runs[params["run_id"]]
        if action == "detail":
            value = copy.deepcopy(run)
            value["events"] = [
                event for event in value["events"] if event["sequence"] > params["after"]
            ]
            return value
        if action == "chat":
            if not isinstance(params.get("text"), str) or not params["text"].strip():
                raise ValueError("请输入消息内容")
            for sender, recipient, text in [
                ("user", "interaction", params["text"]),
                (
                    "interaction",
                    "user",
                    "已记录你的补充要求。你可以继续完善约束，或开始研究。此回复为界面验收用的模拟数据。",
                ),
            ]:
                run["messages"].append(
                    {
                        "message_id": f"m{len(run['messages']) + 1}",
                        "sender": sender,
                        "recipient": recipient,
                        "text": text,
                        "created_at": datetime.now(UTC).isoformat(),
                    }
                )
            run["brief"]["instructions"] += "\n" + params["text"]
            run["intake"].update(status="ready", summary="只修改 skills，固定评测条件。")
            self.event(run, "message", text="用户补充研究要求")
        elif action == "start":
            self.populate(run)
        elif action in {"pause", "resume", "stop"}:
            run["status"] = {"pause": "paused", "resume": "running", "stop": "stopped"}[action]
            if action == "resume":
                run["worker_alive"] = True
            self.event(
                run, "run_" + {"pause": "paused", "resume": "resumed", "stop": "stopped"}[action]
            )
        elif action == "retry":
            for agent in run["agents"]:
                if agent["agent_id"] == params["agent_id"]:
                    agent.update(state="waiting", error=None)
            self.event(run, "agent_retry_requested")
        else:
            raise ValueError("未知操作")
        return {"run_id": run["run_id"], "status": run["status"]}


class WorkbenchHttpTest(unittest.TestCase):
    def setUp(self) -> None:
        self.backend = FixtureBackend()
        self.server = WorkbenchServer(self.backend, port=0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.backend.close)
        self.addCleanup(self.close_server)
        status, _, body = self.request("GET", "/api/bootstrap")
        self.assertEqual(status, 200)
        self.token = json.loads(body)["csrf_token"]

    def close_server(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def test_bootstrap_only_exposes_execution_capabilities(self) -> None:
        for allow_simulation, default_simulated, has_config in (
            (True, True, False),
            (True, False, True),
            (False, False, True),
            (False, False, False),
        ):
            self.backend.bootstrap.update(
                allow_simulation=allow_simulation,
                default_simulated=default_simulated,
                has_config=has_config,
            )
            status, _, body = self.request("GET", "/api/bootstrap")
            self.assertEqual(status, 200)
            self.assertEqual(
                json.loads(body),
                {
                    "csrf_token": self.token,
                    "allow_simulation": allow_simulation,
                    "default_simulated": default_simulated,
                    "has_config": has_config,
                },
            )
            self.assertNotIn(PRIVATE, body.decode())

    def request(
        self, method: str, path: str, data: Any = None, *, headers: dict[str, str] | None = None
    ) -> tuple[int, dict[str, str], bytes]:
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        request_headers = {"Origin": self.server.url}
        body = None
        if data is not None:
            body = json.dumps(data).encode()
            request_headers.update({"Content-Type": "application/json", "X-RSI-Token": self.token})
        request_headers.update(headers or {})
        connection.request(method, path, body=body, headers=request_headers)
        response = connection.getresponse()
        result = response.status, dict(response.getheaders()), response.read()
        connection.close()
        return result

    def create(self) -> str:
        status, _, body = self.request(
            "POST",
            "/api/runs",
            {"message": "检查可复用 skills", "message_id": "first"},
        )
        self.assertEqual(status, 200)
        return str(json.loads(body)["run_id"])

    def test_conversation_graph_and_controls_over_real_http(self) -> None:
        status, headers, body = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn("研究工作台", body.decode())
        self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])
        for asset in ("/app.js", "/dropdown.js", "/style.css"):
            self.assertEqual(self.request("GET", asset)[0], 200)
        run_id = self.create()
        self.assertEqual(
            self.request(
                "POST",
                f"/api/runs/{run_id}/chat",
                {"text": "只修改 skills", "message_id": "user_1"},
            )[0],
            200,
        )
        before = json.loads(self.request("GET", f"/api/runs/{run_id}")[2])
        self.assertEqual(before["status"], "planned")
        self.assertIsNone(before["baseline"])
        self.assertIsNone(before["best"])
        self.assertIn("只修改 skills", before["brief"]["instructions"])
        self.request("POST", f"/api/runs/{run_id}/start", {})
        detail = json.loads(self.request("GET", f"/api/runs/{run_id}")[2])
        self.assertTrue(detail["simulated"])
        self.assertEqual(len(detail["nodes"]), 3)
        self.assertEqual(detail["nodes"][1]["parent_iteration_id"], "n1")
        self.assertEqual(detail["edges"][0]["kind"], "merge")
        self.assertEqual(detail["best"]["score"], 0.72)
        self.assertIsNone(detail["baseline"])
        self.assertIsNone(detail["final"])
        for action, expected in (("pause", "paused"), ("resume", "running"), ("stop", "stopped")):
            result = self.request("POST", f"/api/runs/{run_id}/{action}", {})
            self.assertEqual(json.loads(result[2])["status"], expected)
        status, _, _ = self.request("POST", f"/api/runs/{run_id}/export", {})
        self.assertEqual(status, 404)

    def test_cursor_and_projection_hide_private_configuration_and_internal_messages(self) -> None:
        run_id = self.create()
        self.request("POST", f"/api/runs/{run_id}/start", {})
        status, _, raw = self.request("GET", f"/api/runs/{run_id}")
        self.assertEqual(status, 200)
        self.assertNotIn(PRIVATE, raw.decode())
        detail = json.loads(raw)
        self.assertNotIn("config", detail)
        self.assertNotIn("private", detail["intake"])
        self.assertNotIn("raw_trace", detail["nodes"][0]["report"])
        self.assertEqual(len(detail["messages"]), 2)
        subsequent = json.loads(
            self.request("GET", f"/api/runs/{run_id}?after={detail['event_cursor']}")[2]
        )
        self.assertEqual(subsequent["events"], [])
        self.assertEqual(len(subsequent["nodes"]), 3)
        self.assertNotIn(PRIVATE, self.request("GET", "/api/runs")[2].decode())

    def test_question_identity_and_reply_routing_cross_the_http_boundary(self) -> None:
        run_id = self.create()
        run = self.backend.runs[run_id]
        question = {
            "message_id": "rsi_question",
            "agent_id": "rsi",
            "question": "模型返回 401，请修复访问权限后告诉我。",
            "created_at": datetime.now(UTC).isoformat(),
        }
        run["waiting_for_user"] = [{**question, "private": PRIVATE}]
        run["messages"].append(
            {
                "message_id": "user_reply",
                "sender": "user",
                "recipient": "rsi",
                "text": "已恢复",
                "reply_to": question["message_id"],
                "private": PRIVATE,
            }
        )
        status, _, raw = self.request("GET", f"/api/runs/{run_id}")
        self.assertEqual(status, 200)
        self.assertNotIn(PRIVATE, raw.decode())
        detail = json.loads(raw)
        self.assertEqual(detail["waiting_for_user"], [question])
        self.assertEqual(detail["messages"][-1]["reply_to"], question["message_id"])
        params = {
            "text": "已恢复",
            "message_id": "reply",
            "reply_to": question["message_id"],
            "recipient": "rsi",
        }
        self.assertEqual(self.request("POST", f"/api/runs/{run_id}/chat", params)[0], 200)
        self.assertEqual(self.backend.calls[-1], ("chat", {**params, "run_id": run_id}))

    def test_cross_site_host_and_missing_token_rejected_before_backend(self) -> None:
        count = len(self.backend.calls)
        for headers in (
            {"Origin": "https://attacker.example"},
            {"Host": "attacker.example"},
            {"X-RSI-Token": ""},
            {"Sec-Fetch-Site": "cross-site"},
        ):
            with self.subTest(headers=headers):
                self.assertEqual(
                    self.request("POST", "/api/runs", {"objective": "unsafe"}, headers=headers)[0],
                    403,
                )
        self.assertEqual(len(self.backend.calls), count)
        self.assertEqual(self.request("OPTIONS", "/api/runs")[0], 403)
        self.assertEqual(
            self.request("GET", "/api/bootstrap", headers={"Origin": "https://attacker.example"})[
                0
            ],
            403,
        )

    def test_no_arbitrary_file_routes_or_export_destination(self) -> None:
        run_id = self.create()
        for path in ("/api/config", f"/api/runs/{run_id}/sessions", "/.env", "/session.json"):
            self.assertEqual(self.request("GET", path)[0], 404)
        self.assertEqual(self.request("GET", "/%2e%2e/.env")[0], 400)
        self.assertEqual(self.request("GET", f"/api/runs/{run_id}?after=-1")[0], 400)
        self.request(
            "POST", f"/api/runs/{run_id}/export", {"destination": "/arbitrary", "path": "/secret"}
        )
        self.assertNotEqual(self.backend.calls[-1][0], "export")

    def test_body_and_content_type_validation(self) -> None:
        self.assertEqual(self.request("POST", "/api/runs", [], headers={})[0], 400)
        self.assertEqual(
            self.request(
                "POST", "/api/runs", {"objective": "x"}, headers={"Content-Type": "text/plain"}
            )[0],
            415,
        )
        self.assertEqual(
            self.request("POST", "/api/runs", {"objective": "x" * MAX_BODY_BYTES})[0], 413
        )
        self.assertEqual(self.request("POST", "/api/runs", {"objective": ""})[0], 400)

    def test_zero_score_is_preserved_as_zero(self) -> None:
        run_id = self.create()
        self.backend.runs[run_id]["best"] = {"score": 0, "candidate_id": "zero", "simulated": True}
        detail = json.loads(self.request("GET", f"/api/runs/{run_id}")[2])
        self.assertEqual(detail["best"]["score"], 0)
        self.assertIsNone(detail["baseline"])


if __name__ == "__main__":
    if sys.argv[1:] == ["--serve"]:
        backend = FixtureBackend(include_demo=True)
        server = WorkbenchServer(backend, port=8877)
        print(server.url, flush=True)
        try:
            server.serve_forever()
        finally:
            server.server_close()
            backend.close()
    else:
        unittest.main()
