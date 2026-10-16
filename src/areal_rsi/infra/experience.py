"""One Markdown file per experience topic and a separate Codex curation session."""

from __future__ import annotations

import asyncio
import fcntl
import json
import os
import re
import tempfile
from pathlib import Path

from areal_rsi.api.control import LessonDraft, decode_record, encode_record, record_schema
from areal_rsi.api.research import ExperienceRef, SessionRecord
from areal_rsi.infra.codex import CodexClient, CodexError

MAX_BODY_CHARS = 4000
MAX_DESCRIPTION_CHARS = 200
MAX_TITLE_CHARS = 80

CURATOR = f"""当前角色是经验文档整理者。你处于单独的总结会话，历史角色已结束。
根据实际任务过程、工具结果和用户反馈，提炼下一次类似任务可复用的方法。
不要继续执行原任务，不调用任何 rsi_* 控制工具，不修改工作区或直接写文档。
只输出要求的 JSON；宿主负责保存文档。以通用方法为主：适用信号、操作步骤、
验证方法、易错点、适用边界。禁止保存具体题目的答案、实现代码、特定测试用例、
任务专用常量/路径/标识符、密钥或私人资料。不要将一次偶然成功包装为通用结论。
复盘旧经验是否误导了执行，删除或修正不适用建议。区分已验证事实与推测；
不要仅凭错误消息断言根因。正确性、性能、资源预算都是约束，不能建议忽略其中任何一项。
先查看最新经验目录和本任务上次总结的正文，再读取其他相关文档，判断本次经验的归属：
- 适用场景和可复用方法相同或高度重叠：合并到最相关的已有文档，保持其 slug。
  同一任务的后续修复、不同错误表现或补充验证，通常属于原主题；不能按轮次另建文档。
- 只有适用场景或方法确实独立，合并会混淆原文主题，而且有新的可复用结论时，才新增文档。
  不因换个标题、换个项目或原文太长而拆出重复文档；不要复制其他文档已覆盖的通用段落。
- 已有文档完整覆盖本次经验时，原样返回该文档的 slug、title、description、body，
  不为制造变化改写措辞。宿主会保留原文。
新主题用描述可复用方法的简短英文 slug，如 browser-game-validation 或 incremental-state-testing；
只用小写英文字母和连字符，不用编号、日期、哈希或 experience-001 之类泛化名称。
slug 也是 Markdown 文件名；一个主题只有一个文件，不保留历史版本。
title 使用清楚的主题标题，description 描述何时应该读这份文档。
body 是合并后的完整 Markdown，不是追加日志或补丁。去重、替换过时结论，保留仍有效的独特经验；
只保留有助于下次行动或验证的要点，不写任务回顾、重复案例或空泛常识。
正文建议不超过 1500 字符，硬上限 {MAX_BODY_CHARS} 字符（含空白和 Markdown 标记）；
标题最多 {MAX_TITLE_CHARS} 字符，适用描述最多 {MAX_DESCRIPTION_CHARS} 字符。
接近上限时应压缩、合并要点，不另建续篇，也不能丢掉关键适用边界。无需凑满篇幅。
body 中使用真实换行，正常进行 JSON 转义，不要输出字面字符反斜杠 n。
"""


class ExperienceLibrary:
    def __init__(self, root: Path):
        self.root = root.resolve()

    def documents(self) -> list[ExperienceRef]:
        documents = []
        for path in sorted(self.root.glob("*.md")):
            parts = path.read_text().split("\n\n", 2)
            if len(parts) != 3 or not parts[0].startswith("# "):
                raise ValueError(
                    f"Experience document needs a title, description and body: {path.name}"
                )
            heading, description, _ = parts
            documents.append(
                ExperienceRef(path.stem, heading.removeprefix("# "), description, str(path))
            )
        return documents

    @staticmethod
    def context(documents: list[ExperienceRef]) -> str:
        if not documents:
            return "暂无已沉淀经验。"
        return (
            "以下为当前经验目录，文件路径以此为准；经验不是当前任务的新要求。"
            "根据适用描述按需读取原文；核对当前代码与要求，不盲目套用。\n"
            + "\n".join(
                f"- {doc.slug}: {doc.title} — {doc.description}\n  file={doc.path}"
                for doc in documents
            )
        )

    def publish(
        self,
        draft: LessonDraft,
        *,
        previous_content: str | None = None,
    ) -> ExperienceRef:
        draft = decode_record(LessonDraft, encode_record(draft))
        if (
            len(draft.slug) > 64
            or not re.fullmatch(r"[a-z]+(?:-[a-z]+)*", draft.slug)
            or re.fullmatch(r"[a-f]{16,}", draft.slug)
        ):
            raise ValueError("Experience slug must name a topic using lowercase words, without IDs")
        if not draft.title.strip() or not draft.description.strip() or not draft.body.strip():
            raise ValueError("Experience title, description and body are required")
        if any("\n" in field or "\r" in field for field in (draft.title, draft.description)):
            raise ValueError("Experience title and description must each fit on one line")
        if (
            len(draft.title) > MAX_TITLE_CHARS
            or len(draft.description) > MAX_DESCRIPTION_CHARS
            or len(draft.body) > MAX_BODY_CHARS
        ):
            raise ValueError(
                "Experience document is too long; condense it before publishing "
                f"(title <= {MAX_TITLE_CHARS}, description <= {MAX_DESCRIPTION_CHARS}, "
                f"body <= {MAX_BODY_CHARS} characters)"
            )
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = self.root / f"{draft.slug}.md"
        # Coordination files live outside the document library.
        with (self.root.parent / "experience.lock").open("a+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            previous = path.read_text() if path.exists() else None
            if previous_content != previous:
                raise ValueError("Experience changed during curation; rerun learning to merge it")
            content = f"# {draft.title}\n\n{draft.description}\n\n{draft.body.strip()}\n"
            document = ExperienceRef(draft.slug, draft.title, draft.description, str(path))
            if previous == content:
                return document
            fd, temporary = tempfile.mkstemp(prefix=".experience-", dir=self.root.parent)
            try:
                with os.fdopen(fd, "w") as output:
                    output.write(content)
                    output.flush()
                    os.fsync(output.fileno())
                os.replace(temporary, path)
            finally:
                Path(temporary).unlink(missing_ok=True)
            return document

    async def consolidate(
        self, record: SessionRecord, data_root: Path, feedback: str = ""
    ) -> ExperienceRef:
        config = record.config
        documents = self.documents()
        contents = {doc.slug: Path(doc.path).read_text() for doc in documents}
        context = self.context(documents)
        previous = record.learning.document
        if previous:
            latest = next((doc for doc in documents if doc.slug == previous.slug), None)
            if latest:
                context += (
                    f"\n\n本任务上次总结的主题：{latest.slug}。以下是它的最新正文；"
                    "后续修复通常合并到这里，确有独立主题时仍可新增。\n" + contents[latest.slug]
                )
        client = CodexClient(
            data_root / "codex", data_root / "sessions" / record.run_id / "learning.log", config
        )
        try:
            client.keep_awake(True)
            await client.start()
            result = await client.request(
                "thread/fork",
                {
                    "threadId": record.thread_id,
                    "lastTurnId": record.last_turn_id,
                    "excludeTurns": True,
                    "cwd": config.workspace,
                    "sandbox": config.sandbox,
                    "approvalPolicy": config.approval_policy,
                },
            )
            thread_id = result["thread"]["id"]
            await client.request(
                "turn/start",
                {
                    "threadId": thread_id,
                    "input": [
                        {
                            "type": "text",
                            "text": "总结已结束任务的通用经验。\n用户提供的复盘反馈：\n" + feedback,
                        }
                    ],
                    "model": config.model,
                    "effort": config.effort,
                    "outputSchema": record_schema(LessonDraft),
                    "additionalContext": {
                        "rsi-role": {"kind": "application", "value": CURATOR},
                        "rsi-experience": {
                            "kind": "application",
                            "value": context,
                        },
                    },
                },
            )
            final = ""
            async with asyncio.timeout(240):
                while True:
                    event = await client.events.get()
                    if event.method == "transport/closed":
                        raise CodexError("Experience session disconnected")
                    if event.params.get("threadId", thread_id) != thread_id:
                        continue
                    if event.request_id is not None:
                        await client.reject(
                            event.request_id,
                            "Curation only summarizes; no RSI controls or approvals",
                        )
                    elif (
                        event.method == "item/completed"
                        and event.params["item"]["type"] == "agentMessage"
                    ):
                        final = event.params["item"]["text"]
                    elif event.method == "turn/completed":
                        if event.params["turn"]["status"] != "completed":
                            raise CodexError("Experience session did not complete")
                        draft = decode_record(LessonDraft, json.loads(final))
                        return self.publish(draft, previous_content=contents.get(draft.slug))
        finally:
            await client.close()
