"""Run-owned collaboration records and immutable products.

One coordinator owns a run's execution lock. Immutable records are written before
mutable indexes; a retry with the same identity is safe. Events are separate
atomic files so a partial JSONL append cannot lose subsequent progress.
"""

from __future__ import annotations

import fcntl
import fnmatch
import hashlib
import json
import threading
from pathlib import Path
from typing import Any, TypeVar, cast
from urllib.parse import unquote, urlsplit

from areal_rsi.api.base import ExperimentRun, RecordRef
from areal_rsi.api.research import (
    CandidateSnapshot,
    EvidenceRecord,
    ResearchEvent,
    ResearchReport,
    TaskBrief,
    VerifiedResult,
)
from areal_rsi.base.runs import FileRunStore
from areal_rsi.base.storage import (
    FileContentStore,
    decode,
    encode,
    json_bytes,
    new_id,
    now,
    safe_name,
    safe_relative,
    write_json,
)

T = TypeVar("T")


class ResearchStore:
    def __init__(self, data_root: Path, run_id: str):
        self.runs = FileRunStore(data_root)
        self.paths = self.runs.paths(run_id)
        self.root = self.paths.root
        self.run_id = run_id
        self.content = FileContentStore(data_root / "content")
        self._lock = threading.RLock()

    def put(self, category: str, key: str, value: Any, *, immutable: bool = True) -> RecordRef:
        path = self.root / safe_name(category) / f"{safe_name(key)}.json"
        write_json(path, value, immutable=immutable)
        return self.paths.reference(path)

    def get(self, category: str, key: str, kind: type[T]) -> T:
        path = self.root / safe_name(category) / f"{safe_name(key)}.json"
        return self.decode_record(kind, json.loads(path.read_text()))

    @staticmethod
    def decode_record(kind: type[T], value: dict[str, Any]) -> T:
        if kind is ResearchReport:
            return cast(T, ResearchReport.from_record(value))
        return cast(T, decode(kind, value))

    def records(self, category: str, kind: type[T]) -> list[T]:
        return [
            self.decode_record(kind, json.loads(p.read_text()))
            for p in sorted((self.root / safe_name(category)).glob("*.json"))
        ]

    def run(self) -> ExperimentRun:
        run = self.runs.get(self.run_id, ExperimentRun)
        if run is None:
            raise FileNotFoundError(self.run_id)
        return run

    def save_run(self, run: ExperimentRun) -> None:
        self.runs.save(self.run_id, run, immutable=False)

    def brief(self) -> TaskBrief:
        ref = self.run().brief_ref
        if ref is None:
            raise ValueError("This run has no research brief")
        return decode(TaskBrief, self.runs.read_json(ref))  # type: ignore[no-any-return]

    def contract_digest(self) -> str:
        contract = self.brief().verification
        if contract is None:
            raise ValueError("This run has no verification contract")
        return hashlib.sha256(json_bytes(contract)).hexdigest()

    def event(
        self,
        kind: str,
        *,
        agent_id: str | None = None,
        iteration_id: str | None = None,
        **data: Any,
    ) -> ResearchEvent:
        directory = self.root / "coordination"
        directory.mkdir(parents=True, exist_ok=True)
        # Different ResearchStore objects (including UI/operator processes) may
        # append concurrently. A per-instance thread lock alone cannot serialize
        # their read/allocate/write sequence.
        with self._lock, (directory / "events.lock").open("a+") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            try:
                sequence = self._last_event_sequence() + 1
                event = ResearchEvent(sequence, kind, now(), agent_id, iteration_id, data)
                self.put("events", f"{sequence:012d}", event)
                # Commit the immutable event first. If the process dies here,
                # _last_event_sequence discovers the unindexed tail on recovery.
                write_json(directory / "event-index.json", {"sequence": sequence})
                return event
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    def _last_event_sequence(self) -> int:
        index = self.root / "coordination" / "event-index.json"
        sequence: int | None = None
        if index.exists():
            try:
                value = json.loads(index.read_text()).get("sequence")
                if type(value) is int and value >= 0:
                    sequence = value
            except (ValueError, AttributeError):
                pass
        directory = self.root / "events"
        if sequence is None or (
            sequence > 0 and not (directory / f"{sequence:012d}.json").is_file()
        ):
            # Bootstrap legacy archives once; do not decode event bodies.
            sequence = max((int(p.stem) for p in directory.glob("*.json")), default=0)
        while (directory / f"{sequence + 1:012d}.json").is_file():
            sequence += 1
        return sequence

    def events(self, after: int = 0, *, limit: int | None = None) -> list[ResearchEvent]:
        # Event filenames encode their immutable sequence. Skip acknowledged
        # files before reading/decoding, rather than reparsing the full archive
        # on every workbench poll.
        result = []
        directory = self.root / "events"
        last = self._last_event_sequence()
        start = max(0, after)
        if limit is not None:
            if not 1 <= limit <= 1000:
                raise ValueError("事件数量须为 1 到 1000")
            start = max(start, last - limit)
        for sequence in range(start + 1, last + 1):
            path = directory / f"{sequence:012d}.json"
            if path.is_file():  # Preserve support for legacy archives with gaps.
                result.append(self.decode_record(ResearchEvent, json.loads(path.read_text())))
        return result

    def record_reference(self, ref: RecordRef) -> RecordRef:
        """Resolve only public run records, never private verifier data or sessions.

        Role-specific sharing/grants are checked by the coordinator on top of this
        common boundary. Knowing a private URI is not an access grant.
        """
        uri = urlsplit(ref.uri)
        allowed = {
            "briefs",
            "assignments",
            "reports",
            "evidence",
            "materials",
            "candidates",
            "withdrawals",
            "verifications",
            "executions",
        }
        if not uri.scheme and not uri.netloc and not uri.query and not uri.fragment:
            # Resolve exact IDs only within this run. Do not guess misspellings,
            # search other runs, or turn an ID into an authorization grant.
            if "/" in ref.uri:
                parts = ref.uri.split("/")
                if len(parts) != 2 or parts[0] not in allowed:
                    raise ValueError("Reference is outside this run's public research records")
                identifier = safe_name(parts[1].removesuffix(".json"))
                relative_record = self.root / parts[0] / f"{identifier}.json"
                if not relative_record.is_file():
                    raise ValueError("记录不存在；请用 list_records 核对引用")
                return self.paths.reference(relative_record)
            identifier = safe_name(ref.uri.removesuffix(".json"))
            matches = [
                self.root / category / f"{identifier}.json"
                for category in allowed
                # Withdrawals annotate candidates under the same storage key;
                # they require a qualified reference, not a second entity ID.
                if category != "withdrawals"
                and (self.root / category / f"{identifier}.json").is_file()
            ]
            if len(matches) != 1:
                raise ValueError("记录 ID 不存在或不唯一；请用 list_records 核对完整 URI")
            return self.paths.reference(matches[0])
        path = safe_relative(unquote(uri.path.lstrip("/")))
        if (
            uri.scheme != "run"
            or uri.netloc != self.run_id
            or uri.query
            or uri.fragment
            or len(path.parts) != 2
            or path.parts[0] not in allowed
        ):
            raise ValueError("Reference is outside this run's public research records")
        if not path.name.endswith(".json"):
            complete = path.with_name(path.name + ".json")
            if (self.root / complete).is_file():
                path = complete
        return self.paths.reference(self.root / path)

    def public_record(self, ref: RecordRef) -> Any:
        return self.runs.read_json(self.record_reference(ref))

    def evidence(self, agent_id: str) -> list[EvidenceRecord]:
        records = self.records("evidence", EvidenceRecord)
        by_uri = {
            self.paths.reference(self.root / "evidence" / f"{item.evidence_id}.json").uri: item
            for item in records
        }
        pending = [
            uri
            for uri, item in by_uri.items()
            if agent_id == "rsi" or item.agent_id == agent_id or item.shared
        ]
        pending.extend(
            ref.uri
            for actor in self.run().research_agents
            if actor.agent_id == agent_id
            for node in actor.iterations
            for ref in node.references
        )
        visible = set()
        while pending:
            uri = pending.pop()
            if uri in visible or uri not in by_uri:
                continue
            visible.add(uri)
            # An explicitly handed-off evidence record includes its supporting
            # evidence. Private paths and other runs remain outside this index.
            pending.extend(ref.uri for ref in by_uri[uri].references)
        return [item for uri, item in by_uri.items() if uri in visible]

    def publish_evidence(
        self, evidence_id: str, *, limitations: str, publication_id: str | None = None
    ) -> EvidenceRecord:
        """Publication is a new record, preserving the researcher's original claim."""
        source = self.get("evidence", evidence_id, EvidenceRecord)
        if publication_id is not None:
            path = self.root / "evidence" / f"{safe_name(publication_id)}.json"
            if path.exists():
                return self.get("evidence", publication_id, EvidenceRecord)
        published = EvidenceRecord(
            publication_id or new_id("evidence"),
            "rsi",
            source.title,
            source.summary,
            now(),
            source.iteration_id,
            (
                self.paths.reference(self.root / "evidence" / f"{safe_name(evidence_id)}.json"),
                *source.references,
            ),
            True,
            limitations,
            evidence_id,
        )
        self.put("evidence", published.evidence_id, published)
        self.event("evidence_shared", evidence_id=published.evidence_id, title=published.title)
        return published

    async def snapshot(
        self,
        workspace: Path,
        *,
        agent_id: str,
        description: str,
        parent_candidate_id: str | None = None,
        candidate_id: str | None = None,
        scope_base_candidate_id: str | None = "",
    ) -> CandidateSnapshot:
        content = await self.content.snapshot(workspace)
        candidate = CandidateSnapshot(
            candidate_id or new_id("candidate"),
            content,
            now(),
            agent_id,
            description,
            parent_candidate_id,
        )
        if parent_candidate_id is not None:
            parent = self.get("candidates", parent_candidate_id, CandidateSnapshot)
            previous = await self.content.read_json(parent.content)
        else:
            previous = {"files": {}}
        current = await self.content.read_json(content)
        assert isinstance(previous, dict) and isinstance(current, dict)
        old_files, files = previous["files"], current["files"]
        assert isinstance(old_files, dict) and isinstance(files, dict)
        changed = [
            name
            for name in old_files.keys() | files.keys()
            if old_files.get(name) != files.get(name)
        ]
        if agent_id != "operator":
            # Provenance follows the last saved/adopted candidate, while the
            # authorization boundary stays the run's original seed (or empty).
            # Low-level callers retain the previous parent-comparison default.
            scope_base = (
                parent_candidate_id if scope_base_candidate_id == "" else scope_base_candidate_id
            )
            await self.check_candidate_scope(candidate, scope_base)
        self.put("candidates", candidate.candidate_id, candidate)
        self.event(
            "candidate_saved",
            agent_id=agent_id,
            candidate_id=candidate.candidate_id,
            description=description,
            changed_files=changed,
        )
        return candidate

    async def check_candidate_scope(
        self, candidate: CandidateSnapshot, scope_base_candidate_id: str | None
    ) -> None:
        current = await self.content.read_json(candidate.content)
        previous = (
            await self.content.read_json(
                self.get("candidates", scope_base_candidate_id, CandidateSnapshot).content
            )
            if scope_base_candidate_id is not None
            else {"files": {}}
        )
        assert isinstance(current, dict) and isinstance(previous, dict)
        files, old_files = current["files"], previous["files"]
        assert isinstance(files, dict) and isinstance(old_files, dict)
        patterns = self.brief().limits.allowed_paths
        outside = sorted(
            name
            for name in old_files.keys() | files.keys()
            if old_files.get(name) != files.get(name)
            and not any(fnmatch.fnmatchcase(name, pattern) for pattern in patterns)
        )
        if outside:
            detail = ", ".join(outside[:20])
            if len(outside) > 20:
                detail += f" (and {len(outside) - 20} more)"
            raise ValueError("Candidate changes files outside the user's allowed_paths: " + detail)

    async def export(self, candidate_id: str, destination: Path) -> Path:
        candidate = self.get("candidates", candidate_id, CandidateSnapshot)
        return await self.content.materialize(candidate.content, destination)

    def record_verification(self, result: VerifiedResult) -> RecordRef:
        """Trusted adapter entry point; this is intentionally not an agent tool."""
        self.get("candidates", result.candidate_id, CandidateSnapshot)
        if result.contract_digest != self.contract_digest():
            raise ValueError("Verification uses a different comparison contract")
        ref = self.put("verifications", result.result_id, result)
        self.event("verification_completed", result=encode(result))
        return ref

    def withdrawals(self) -> list[dict[str, Any]]:
        return [
            json.loads(path.read_text())
            for path in sorted((self.root / "withdrawals").glob("*.json"))
        ]

    def is_withdrawn(self, candidate_id: str) -> bool:
        candidate = self.get("candidates", candidate_id, CandidateSnapshot)
        return any(
            item["content_digest"] == candidate.content.digest for item in self.withdrawals()
            if not item.get("scope_agent_id") or item["scope_agent_id"] == candidate.source_agent_id
        )

    def withdraw_candidate(
        self,
        candidate_id: str,
        reason: str,
        references: tuple[RecordRef, ...],
        *,
        simulated_run: bool = False,
        agent_id: str = "rsi",
        scope_agent_id: str | None = None,
    ) -> RecordRef:
        """Remove a known-defective artifact from selection; retain its measurements."""
        candidate = self.get("candidates", candidate_id, CandidateSnapshot)
        if not reason.strip():
            raise ValueError("Candidate withdrawal needs a concrete reason")
        path = self.root / "withdrawals" / f"{candidate_id}.json"
        if not path.exists():
            self.put(
                "withdrawals",
                candidate_id,
                {
                    "candidate_id": candidate_id,
                    "content_digest": candidate.content.digest,
                    "agent_id": agent_id,
                    "reason": reason,
                    "created_at": now(),
                    "references": encode(references),
                    **({"scope_agent_id": scope_agent_id} if scope_agent_id else {}),
                },
            )
            self.event(
                "candidate_withdrawn", agent_id=agent_id, candidate_id=candidate_id, reason=reason
            )
        # Reconcile on replay too: a crash may follow the immutable withdrawal.
        run = self.run()
        if run.final_result_ref and self.is_withdrawn(
            self.public_record(run.final_result_ref)["candidate_id"]
        ):
            run.final_result_ref = None
        if run.best_result_ref and self.is_withdrawn(
            self.public_record(run.best_result_ref)["candidate_id"]
        ):
            run.best_result_ref = None
        self.save_run(run)
        if self.brief().verification is not None:
            for result in self.records("verifications", VerifiedResult):
                self.consider_best(result.result_id, simulated_run=simulated_run)
        return self.paths.reference(path)

    def consider_best(self, result_id: str, *, simulated_run: bool = False) -> bool:
        result = self.get("verifications", result_id, VerifiedResult)
        if (
            result.contract_digest != self.contract_digest()
            or result.status != "succeeded"
            or not result.passed
            or result.phase != "search"
            or result.simulated != simulated_run
            or self.is_withdrawn(result.candidate_id)
        ):
            return False
        contract = self.brief().verification
        assert contract is not None and result.score is not None
        run = self.run()
        if run.best_result_ref is not None:
            best = decode(VerifiedResult, self.public_record(run.best_result_ref))
            if best.score is None:
                raise ValueError("Saved best result has no score")
            if (contract.direction == "maximize" and result.score <= best.score) or (
                contract.direction == "minimize" and result.score >= best.score
            ):
                return False
        run.best_result_ref = self.paths.reference(
            self.root / "verifications" / f"{result_id}.json"
        )
        self.save_run(run)
        self.event(
            "best_updated",
            result_id=result_id,
            candidate_id=result.candidate_id,
            score=result.score,
            simulated=result.simulated,
        )
        return True
