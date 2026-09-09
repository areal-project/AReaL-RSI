"""Create remote configuration candidates, inheriting the platform-owned runtime."""

from __future__ import annotations

import copy
import json
import re
from typing import Any

from areal_rsi.api.base import ContentRef, HarnessChange, HarnessVersion
from areal_rsi.base.storage import FileContentStore, json_bytes, write_json
from areal_rsi.modules.remote.envarena.client import ArenaClient, fingerprint, redact, reference
from areal_rsi.modules.remote.envarena.config import ArenaConfig

REMOTE_HARNESS_TYPE = "application/vnd.areal-rsi.arena-harness+json"
REMOTE_CHANGE_TYPE = "application/vnd.areal-rsi.arena-change+json"


async def snapshot(client: ArenaClient, content: FileContentStore, ref: Any) -> ContentRef:
    value = await client.version("harnesses", ref)
    return await content.put_bytes(
        json_bytes(
            {
                "backend": "envarena",
                "ref": reference(ref),
                "content_hash": value.get("content_hash"),
                "spec": redact(value["spec"]),
            }
        ),
        media_type=REMOTE_HARNESS_TYPE,
    )


def validate_change(value: Any, allowed: tuple[str, ...]) -> dict[str, Any]:
    if not isinstance(value, dict) or not value or set(value) - set(allowed):
        raise ValueError("候选只能修改远端声明支持的 skill/tool/system_prompt/mcp")
    if len(json_bytes(value)) > 200_000:
        raise ValueError("候选配置超过 200 KB")
    # Secret-bearing environment values must stay in remote service configuration.
    if redact(value) != value:
        raise ValueError("候选不能携带凭证，请引用远端已配置的资源")
    if "system_prompt" in value:
        prompt = value["system_prompt"]
        if isinstance(prompt, dict) and set(prompt) == {"append"}:
            prompt = prompt["append"]
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError("system_prompt 必须是非空文本或 {append: 非空文本}")
    for name in ("skills", "tools"):
        if name not in value:
            continue
        if not isinstance(value[name], list) or len(value[name]) > 16:
            raise ValueError(f"{name} 必须是最多 16 个资源的列表")
        for item in value[name]:
            if isinstance(item, str) or (
                isinstance(item, dict) and set(item) == {"key", "version"}
            ):
                reference(item)
            elif (
                name == "skills"
                and isinstance(item, dict)
                and set(item) == {"name", "description", "content"}
            ):
                if any(not isinstance(v, str) or not v.strip() for v in item.values()):
                    raise ValueError("新 Skill 需要 name/description/content 文本")
                if not re.fullmatch(r"[a-z0-9-]+", item["name"]):
                    raise ValueError("Skill name 必须为小写字母、数字和连字符")
            elif (
                name == "tools"
                and isinstance(item, dict)
                and set(item) == {"name", "description", "mcp_config"}
            ):
                validate_mcp(item["mcp_config"])
                if any(
                    not isinstance(item[k], str) or not item[k].strip()
                    for k in ("name", "description")
                ):
                    raise ValueError("新 Tool 需要 name/description")
            else:
                raise ValueError(f"非法 {name} 资源声明")
    if "mcp" in value:
        validate_mcp(value["mcp"])
    return value


def validate_mcp(value: Any) -> None:
    if (
        not isinstance(value, dict)
        or set(value) != {"mcpServers"}
        or not isinstance(value["mcpServers"], dict)
    ):
        raise ValueError("MCP 配置必须包含 mcpServers 对象")
    for name, server in value["mcpServers"].items():
        if (
            not name
            or not isinstance(server, dict)
            or not (server.get("command") or server.get("url"))
        ):
            raise ValueError("MCP server 需要远端 command 或 url")


class ArenaHarnessBuilder:
    def __init__(self, client: ArenaClient, content: FileContentStore, config: ArenaConfig):
        self.client, self.content, self.config = client, content, config

    async def build(self, base: HarnessVersion, change: HarnessChange) -> ContentRef:
        if (
            base.content.media_type != REMOTE_HARNESS_TYPE
            or change.patch.media_type != REMOTE_CHANGE_TYPE
        ):
            raise ValueError(
                "remote Harness 需要配置快照和声明式变更，不接受本地运行时包或文件 patch"
            )
        saved = json.loads(await self.content.read_text(base.content))
        proposal = validate_change(
            json.loads(await self.content.read_text(change.patch)), self.config.allowed_changes
        )
        original = await self.client.version("harnesses", saved["ref"])
        if original.get("content_hash") != saved["content_hash"]:
            raise ValueError("远端已发布 Harness 的内容身份已改变")
        spec = copy.deepcopy(original["spec"])
        if "system_prompt" in proposal:
            prompt = proposal["system_prompt"]
            if isinstance(prompt, dict):
                previous = spec.get("system_prompt") or {}
                if previous.get("kind", "inline") != "inline":
                    raise ValueError("append 需要 inline prompt；远端对象 prompt 请显式替换")
                prompt = str(previous.get("content", "")) + "\n\n" + prompt["append"]
            spec["system_prompt"] = {"kind": "inline", "content": prompt}
        for name, kind, field in (
            ("skills", "skills", "skill_refs"),
            ("tools", "tools", "tool_refs"),
        ):
            if name not in proposal:
                continue
            refs = []
            for item in proposal[name]:
                if isinstance(item, str) or set(item) == {"key", "version"}:
                    ref = reference(item)
                    await self.client.version(kind, ref)
                elif name == "skills":
                    ref = await self.client.import_skill(self.config.namespace, **item)
                else:
                    ref = await self.client.ensure_version(
                        "tools",
                        self.config.namespace,
                        {
                            "manifest": {
                                "name": item["name"],
                                "description": item["description"],
                                "kind": "mcp",
                            },
                            "mcp_config_inline": item["mcp_config"],
                        },
                    )
                if ref in refs:
                    raise ValueError("候选含重复资源引用")
                refs.append(ref)
            spec[field] = refs
        if "mcp" in proposal:
            # Arena materializes this configuration into the selected runtime's private directory.
            identity = fingerprint(
                {"base_url": self.client.connection.base_url, "mcp": proposal["mcp"]}
            )
            receipt = self.content.root.parent / "remote" / "uploads" / f"{identity}.json"
            if receipt.exists():
                uri = json.loads(receipt.read_text())["uri"]
            else:
                uri = await self.client.upload("mcp.json", json_bytes(proposal["mcp"]))
                write_json(receipt, {"uri": uri}, immutable=True)
            spec.setdefault("materials", {})["mcp_config"] = {"uri": uri}
        if spec == original["spec"]:
            raise ValueError("候选未改变远端配置")
        if spec.get("runtime") != original["spec"].get("runtime"):
            raise ValueError("候选禁止改变远端 Runtime")
        # This also preserves agent/env, resources, TTL, and the output contract.
        ref = await self.client.ensure_version("harnesses", self.config.namespace, spec)
        published = await self.client.version("harnesses", ref)
        if published["spec"].get("runtime") != original["spec"].get("runtime"):
            raise ValueError("远端候选没有继承 Runtime")
        return await snapshot(self.client, self.content, ref)
