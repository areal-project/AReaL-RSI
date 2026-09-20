"""Content-addressed evidence manifests and explicit, isolated file exports."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from pathlib import Path

from areal_rsi.api.interfaces import ContentStore
from areal_rsi.api.records import (
    EVIDENCE_BUNDLE_MEDIA_TYPE,
    ContentRef,
    EvidenceBundle,
    EvidenceFile,
)
from areal_rsi.infra.storage.files import DIRECTORY_TYPE, decode, encode, safe_relative, write_json


def validate_bundle(bundle: EvidenceBundle) -> None:
    """Reject ambiguous paths before any file is exported into a workspace."""
    names: set[str] = set()
    for entry in bundle.files:
        path = safe_relative(entry.path)
        if (
            not path.parts
            or path.as_posix() != entry.path
            or any(ord(char) < 32 for char in entry.path)
            or path.parts[0].casefold() == "index.json"
        ):
            raise ValueError(f"Invalid evidence path: {entry.path!r}")
        # Case-insensitive volumes must not silently merge two logical files.
        key = entry.path.casefold()
        if key in names:
            raise ValueError("Duplicate evidence path")
        names.add(key)
    for name in names:
        if any(parent.as_posix() in names for parent in Path(name).parents):
            raise ValueError("Evidence file/directory collision")


class FileEvidenceStore:
    """Keep immutable references centrally; give each agent a disposable file view."""

    def __init__(self, content: ContentStore):
        self.content = content

    async def put(self, bundle: EvidenceBundle) -> ContentRef:
        # Store references only; existing outputs and traces are not copied or rewritten.
        validate_bundle(bundle)
        files = []
        for entry in bundle.files:
            if entry.content.media_type != DIRECTORY_TYPE:
                files.append(entry)
                continue
            # Snapshot manifests contain flat relative file names. Expand before
            # granting access so each child can be authorized independently.
            manifest = await self.content.read_json(entry.content)
            children = manifest.get("files") if isinstance(manifest, dict) else None
            if not isinstance(children, dict):
                raise ValueError("Invalid evidence directory snapshot")
            for name, value in children.items():
                path = safe_relative(name)
                if path.as_posix() != name or not path.parts:
                    raise ValueError("Invalid evidence snapshot path")
                child = decode(ContentRef, value)
                if child.media_type == DIRECTORY_TYPE:
                    raise ValueError("Evidence snapshots must contain file references")
                files.append(
                    EvidenceFile(f"{entry.path}/{name}", child, entry.kind, dict(entry.metadata))
                )
        bundle = EvidenceBundle(tuple(files), dict(bundle.metadata))
        validate_bundle(bundle)
        return await self.content.put_text(
            json.dumps(encode(bundle), ensure_ascii=False, sort_keys=True),
            media_type=EVIDENCE_BUNDLE_MEDIA_TYPE,
        )

    async def get(self, ref: ContentRef) -> EvidenceBundle:
        # Payload bytes are verified when exported, so ungranted files need not be fetched.
        if ref.media_type != EVIDENCE_BUNDLE_MEDIA_TYPE:
            raise ValueError("Expected an evidence bundle, not a trajectory bundle")
        bundle: EvidenceBundle = decode(EvidenceBundle, await self.content.read_json(ref))
        validate_bundle(bundle)
        return bundle

    async def materialize(
        self, ref: ContentRef, destination: Path, *, paths: Sequence[str]
    ) -> ContentRef:
        bundle = await self.get(ref)
        selected = set(paths)
        if len(selected) != len(paths) or not selected <= {f.path for f in bundle.files}:
            raise ValueError("Evidence grant contains duplicate or unknown paths")
        # No untrusted process runs in this fresh workspace until export completes.
        destination = destination.absolute()
        if any(parent.is_symlink() for parent in (destination, *destination.parents)):
            raise ValueError("Evidence destination must not traverse symlinks")
        destination.mkdir()
        exported = []
        index = []
        for entry in bundle.files:
            if entry.path not in selected:
                continue
            data = await self.content.read_bytes(entry.content)
            if entry.content.digest != "sha256:" + hashlib.sha256(data).hexdigest():
                raise ValueError("Evidence content digest mismatch")
            target = destination / entry.path
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("xb") as stream:
                stream.write(data)
            exported.append(entry)
            index.append(
                {
                    "path": entry.path,
                    "kind": entry.kind,
                    "metadata": entry.metadata,
                    "media_type": entry.content.media_type,
                    "digest": entry.content.digest,
                    "bytes": len(data),
                }
            )
        # Do not expose backing-store URIs or metadata of ungranted files.
        write_json(destination / "index.json", {"files": index}, immutable=True)
        return await self.put(EvidenceBundle(tuple(exported)))
