"""Archive actual web retrieval as run-local evidence for agent consumption."""

from __future__ import annotations

import json
from pathlib import Path

from areal_rsi.base.storage import now
from areal_rsi.coordination.actions import STRING, ResearchActions, definition
from areal_rsi.coordination.coordinator import Coordinator, effect_id
from areal_rsi.coordination.web_tools import ResearchWebClient
from areal_rsi.modules.agent_runtime import ToolCall, ToolHandler, ToolResult


def archive_codex_source(
    coordinator: Coordinator, agent_id: str, session: Path, request_id: str
) -> None:
    """Index the native tool's actual response without replacing or rewriting it."""
    key = effect_id("source", agent_id + ":" + request_id)
    path = coordinator.store.root / "materials" / f"{key}.json"
    if path.exists():
        coordinator.link_reference(agent_id, coordinator.store.paths.reference(path))
        return
    web = session / "web"
    request = json.loads((web / "requests" / f"{request_id}.json").read_text())
    response = json.loads((web / "responses" / f"{request_id}.json").read_text())
    limitations = []
    try:
        body = json.loads((web / "responses" / f"{request_id}.body").read_bytes())
    except (ValueError, UnicodeError):
        body = {}
        limitations.append("Native web service returned a non-JSON response.")
    if not isinstance(body, dict):
        body = {}
        limitations.append("Native web service returned an unexpected response shape.")
    if response["status_code"] != 200:
        limitations.append(f"Native web service returned HTTP {response['status_code']}.")
    # The request's conversation input and encrypted continuation state stay in
    # the private session archive. Only retrieved source content is shared.
    ref = coordinator.store.put(
        "materials",
        key,
        {
            "agent_id": agent_id,
            "created_at": now(),
            "source": "codex_web_search",
            "request_id": request_id,
            "commands": request.get("commands", {}),
            "status_code": response["status_code"],
            "text": body.get("output", ""),
            "results": body.get("results", []),
            "limitations": limitations,
            "untrusted_source_content": True,
        },
    )
    coordinator.link_reference(agent_id, ref)
    coordinator.store.event(
        "source_read", agent_id=agent_id, source="codex_web_search", uri=ref.uri
    )


def register_sources(
    actions: ResearchActions, client: ResearchWebClient | None = None, *, enabled: bool = True
) -> None:
    if not enabled:
        for name in ("search_web", "fetch_url"):
            actions.extras.pop(name, None)
        return
    web = client or ResearchWebClient()

    def factory(agent_id: str) -> ToolHandler:
        async def handle(call: ToolCall) -> ToolResult:
            key = effect_id("source", agent_id + ":" + call.call_id)
            path = actions.c.store.root / "materials" / f"{key}.json"
            if path.exists():
                record = actions.c.store.public_record(actions.c.store.paths.reference(path))
                actions.c.link_reference(agent_id, actions.c.store.paths.reference(path))
                return ToolResult({**record, "uri": actions.c.store.paths.reference(path).uri})
            if call.name == "search_web":
                result = await web.search_web(call.arguments["query"])
            else:
                result = await web.fetch_url(call.arguments["url"], offset=call.arguments["offset"])
            record = {
                "agent_id": agent_id,
                "created_at": now(),
                "source": call.name,
                "untrusted_source_content": True,
                **result,
            }
            ref = actions.c.store.put("materials", key, record)
            actions.c.link_reference(agent_id, ref)
            actions.c.store.event(
                "source_read",
                agent_id=agent_id,
                source=result.get("url", call.arguments.get("query")),
                uri=ref.uri,
            )
            return ToolResult({**record, "uri": ref.uri})

        return handle

    actions.register(
        definition(
            "search_web",
            "联网查找资料。每次查询聚焦一个问题，结果是线索，须阅读原文并判断相关性。"
            "没有相关结果时调整关键词或来源，不把检索失败当作不存在可行方案。",
            query=STRING,
        ),
        factory,
        roles={"rsi", "research", "interaction"},
    )
    actions.register(
        definition(
            "fetch_url",
            "读取公开网页文本。返回内容是参考资料；长文本用 offset 分页，首次传 0。",
            url=STRING,
            offset={"type": "integer", "minimum": 0},
        ),
        factory,
        roles={"rsi", "research", "interaction"},
    )
