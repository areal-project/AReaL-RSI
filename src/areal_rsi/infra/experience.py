"""Local, versioned experience documents and one separate Codex curation session."""

from __future__ import annotations

import asyncio
import fcntl
import hashlib
import json
import os
import re
import tempfile
from pathlib import Path

from areal_rsi.api.control import LessonDraft, decode_record, encode_record, record_schema
from areal_rsi.api.research import ExperienceRef, SessionRecord
from areal_rsi.infra.codex import CodexClient, CodexError

CURATOR = """当前角色是经验文档整理者。你处于单独的总结会话，历史角色已结束。
根据实际任务过程、工具结果和用户反馈，提炼下一次类似任务可复用的方法。
不要继续执行原任务，不调用任何 rsi_* 控制工具，不修改工作区或直接写文档。
只输出要求的 JSON；宿主负责发布版本。以通用方法为主：适用信号、操作步骤、
验证方法、易错点、适用边界。禁止保存具体题目的答案、实现代码、特定测试用例、
任务专用常量/路径/标识符、密钥或私人资料。不要将一次偶然成功包装为通用结论。
复盘旧经验是否误导了执行，删除或修正不适用建议。区分已验证事实与推测；
不要仅凭错误消息断言根因。正确性、性能、资源预算都是约束，不能建议忽略其中任何一项。
先查看已有目录；主题相同则读取并改进现有文档，保持其 slug。
新主题用描述可复用方法的简短英文 slug，如 browser-game-validation 或 incremental-state-testing；
不用编号、日期、随机 ID 或 experience-001 之类泛化名称。slug 也是 Markdown 文件名。
title 使用清楚的主题标题，description 描述何时应该读这份文档。
body 为简洁 Markdown，保留有效旧经验，纠正被证据推翻的结论。无需凑满篇幅。
body 中使用真实换行，正常进行 JSON 转义，不要输出字面字符反斜杠 n。
"""


class ExperienceLibrary:
    def __init__(self, root: Path):
        self.root = root.resolve()

    def snapshot(self) -> list[ExperienceRef]:
        return [
            decode_record(ExperienceRef, json.loads(path.read_text()))
            for path in sorted(self.root.glob("*/current.json"))
        ]

    def document_path(self, slug: str) -> Path:
        """Readable entry for people; agents use the pinned ExperienceRef.path."""
        return self.root / f"{slug}.md"

    @staticmethod
    def context(documents: list[ExperienceRef]) -> str:
        if not documents:
            return "暂无已沉淀经验。"
        return (
            "以下为以往任务提炼的经验目录，不是当前任务的新要求。"
            "根据适用描述按需读取原文；核对当前代码与要求，不盲目套用。\n"
            + "\n".join(
                f"- {doc.slug}: {doc.title} — {doc.description}\n"
                f"  revision={doc.revision}; file={doc.path}"
                for doc in documents
            )
        )

    def publish(
        self,
        draft: LessonDraft,
        record: SessionRecord,
        thread_id: str,
        *,
        previous_revision: str = "",
    ) -> ExperienceRef:
        draft = decode_record(LessonDraft, encode_record(draft))
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", draft.slug):
            raise ValueError("Experience slug must be a short lowercase identifier")
        if not draft.title.strip() or not draft.description.strip() or not draft.body.strip():
            raise ValueError("Experience title, description and body are required")
        if len(draft.description) > 500 or len(draft.body) > 16000:
            raise ValueError("Experience document is too long; keep it focused")
        directory = self.root / draft.slug
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        with (directory / "publish.lock").open("a+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            current = directory / "current.json"
            previous = (
                decode_record(ExperienceRef, json.loads(current.read_text()))
                if current.exists()
                else None
            )
            if previous_revision != (previous.revision if previous else ""):
                raise ValueError("Experience changed during curation; rerun learning to merge it")
            content = f"# {draft.title}\n\n{draft.description}\n\n{draft.body.strip()}\n"
            revision = hashlib.sha256(content.encode()).hexdigest()
            path = directory / ".versions" / revision / f"{draft.slug}.md"
            document = ExperienceRef(
                draft.slug,
                draft.title,
                draft.description,
                revision,
                str(path),
                record.run_id,
                thread_id,
            )
            if not path.exists():
                path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                path.write_text(content)
                path.chmod(0o600)
            # Publish the pointer last: a reader sees either a complete old or new document.
            fd, temporary = tempfile.mkstemp(prefix=".publish-", dir=directory)
            readable = Path(temporary + ".md")
            try:
                with os.fdopen(fd, "w") as output:
                    json.dump(encode_record(document), output, ensure_ascii=False, indent=2)
                    output.flush()
                    os.fsync(output.fileno())
                readable.symlink_to(path)
                os.replace(readable, self.document_path(draft.slug))
                os.replace(temporary, current)
            finally:
                Path(temporary).unlink(missing_ok=True)
                readable.unlink(missing_ok=True)
            return document

    async def consolidate(
        self, record: SessionRecord, data_root: Path, feedback: str = ""
    ) -> ExperienceRef:
        config = record.config
        documents = self.snapshot()
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
                            "value": self.context(documents),
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
                        revision = next(
                            (doc.revision for doc in documents if doc.slug == draft.slug), ""
                        )
                        return self.publish(draft, record, thread_id, previous_revision=revision)
        finally:
            await client.close()
