#!/usr/bin/env python3
"""Prepare a versioned Claude runtime bundle without changing Arena services."""

import argparse
import copy
import hashlib
import json
import shutil
from pathlib import Path


def prepare(base, claude_binary, output, version, runtime_uri):
    spec = copy.deepcopy(base["spec"])
    if spec["runtime"]["type"] != "claude_code":
        raise ValueError("The source Harness must use claude_code")
    if not runtime_uri.endswith("/"):
        raise ValueError("runtime.uri must be a directory prefix ending in /")
    bundle = output / "runtime"
    bundle.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(Path(__file__).with_name("claude"), bundle / "claude")
    shutil.copyfile(claude_binary, bundle / "claude.real")
    for name in ("claude", "claude.real"):
        (bundle / name).chmod(0o755)
    spec["runtime"] = {"type": "claude_code", "uri": runtime_uri}
    contract = spec.setdefault("io_contract", {})
    globs = contract.setdefault("artifact_globs", [])
    receipt = "$ARENA_OUTPUT_DIR/harness_result.json"
    if receipt not in globs:
        globs.append(receipt)
    env = spec.setdefault("agent", {}).setdefault("env", {})
    env["AREAL_CLAUDE_DEADLINE_RECEIPT"] = "1"
    env["AREAL_CLAUDE_DEADLINE_MARGIN_SEC"] = "30"
    payload = {
        "version": version,
        "storage_ref": base.get("storage_ref", "default"),
        "spec": spec,
    }
    (output / "version.json").write_text(json.dumps(payload, indent=2) + "\n")
    manifest = {}
    for name in ("claude", "claude.real"):
        hasher = hashlib.sha256()
        with (bundle / name).open("rb") as stream:
            while block := stream.read(4 * 1024 * 1024):
                hasher.update(block)
        manifest[name] = hasher.hexdigest()
    (output / "sha256.json").write_text(json.dumps(manifest, indent=2) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-harness", required=True, type=Path)
    parser.add_argument("--claude-binary", required=True, type=Path)
    parser.add_argument("--version", default="5.0.2-areal-deadline.1")
    parser.add_argument("--runtime-uri", default="REPLACE_AFTER_UPLOAD/")
    parser.add_argument("--output", type=Path, default=Path(__file__).parent / "build")
    args = parser.parse_args()
    prepare(
        json.loads(args.base_harness.read_text()),
        args.claude_binary,
        args.output,
        args.version,
        args.runtime_uri,
    )
    print(f"Prepared runtime directory: {args.output / 'runtime'}")
    print(f"Prepared Harness version: {args.output / 'version.json'}")
    if args.runtime_uri == "REPLACE_AFTER_UPLOAD/":
        print("Replace spec.runtime.uri with the uploaded directory URI before publishing.")


if __name__ == "__main__":
    main()
