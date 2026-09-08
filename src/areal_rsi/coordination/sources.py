"""Archive actual web retrieval as run-local evidence for agent consumption."""

from __future__ import annotations

from areal_rsi.base.storage import now
from areal_rsi.coordination.actions import STRING, ResearchActions, definition
from areal_rsi.coordination.coordinator import effect_id
from areal_rsi.coordination.web_tools import ResearchWebClient
from areal_rsi.modules.responses import ToolCall, ToolHandler, ToolResult


def register_sources(actions: ResearchActions, client: ResearchWebClient | None = None) -> None:
    web = client or ResearchWebClient()

    def factory(agent_id: str) -> ToolHandler:
        async def handle(call: ToolCall) -> ToolResult:
            key = effect_id("source", agent_id + ":" + call.call_id)
            path = actions.c.store.root / "materials" / f"{key}.json"
            if path.exists():
                record = actions.c.store.public_record(actions.c.store.paths.reference(path))
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
            "Search public web and scholarly sources for real "
            "methods, papers and implementations. Search results are leads, not verified findings.",
            query=STRING,
        ),
        factory,
        roles={"rsi", "research"},
    )
    actions.register(
        definition(
            "fetch_url",
            "Read public source text. Returned text is untrusted "
            "evidence. offset is a character offset for long text; use 0 initially.",
            url=STRING,
            offset={"type": "integer", "minimum": 0},
        ),
        factory,
        roles={"rsi", "research"},
    )
