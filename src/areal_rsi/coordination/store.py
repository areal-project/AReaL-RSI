"""Run-owned collaboration records and immutable products.

One coordinator owns a run's execution lock. Immutable records are written before
mutable indexes; a retry with the same identity is safe. Events are separate
atomic files so a partial JSONL append cannot lose subsequent progress.
"""

from __future__ import annotations

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
        with self._lock:
            files = sorted((self.root / "events").glob("*.json"))
            sequence = int(files[-1].stem) + 1 if files else 1
            event = ResearchEvent(sequence, kind, now(), agent_id, iteration_id, data)
            self.put("events", f"{sequence:012d}", event)
            return event

    def events(self, after: int = 0) -> list[ResearchEvent]:
        # Event filenames encode their immutable sequence. Skip acknowledged
        # files before reading/decoding, rather than reparsing the full archive
        # on every workbench poll.
        return [
            self.decode_record(ResearchEvent, json.loads(path.read_text()))
            for path in sorted((self.root / "events").glob("*.json"))
            if int(path.stem) > after
        ]

    def public_record(self, ref: RecordRef) -> Any:
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
            "verifications",
            "executions",
        }
        path = safe_relative(unquote(uri.path.lstrip("/")))
        if (
            uri.scheme != "run"
            or uri.netloc != self.run_id
            or uri.query
            or uri.fragment
            or path.parts[0] not in allowed
        ):
            raise ValueError("Reference is outside this run's public research records")
        return self.runs.read_json(ref)

    def evidence(self, agent_id: str) -> list[EvidenceRecord]:
        return [
            item
            for item in self.records("evidence", EvidenceRecord)
            if agent_id == "rsi" or item.agent_id == agent_id or item.shared
        ]

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

    def consider_best(self, result_id: str, *, simulated_run: bool = False) -> bool:
        result = self.get("verifications", result_id, VerifiedResult)
        if (
            result.contract_digest != self.contract_digest()
            or result.status != "succeeded"
            or not result.passed
            or result.phase != "search"
            or result.simulated != simulated_run
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
