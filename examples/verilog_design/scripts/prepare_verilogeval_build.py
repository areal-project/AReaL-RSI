"""Prepare a tools-only Docker context, pinning ARCA's office-network downloads."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path

BASE = "https://antsys-arca-internal-dev.cn-heyuan-alipay-office.oss-alipay.aliyuncs.com/"


@dataclass(frozen=True)
class Artifact:
    name: str
    url: str
    sha256: str


ARTIFACTS = [
    Artifact(
        "autocert_swg_ca.crt",
        BASE + "egress-gw-spanner/ca/v0.2/autocert_swg_ca.crt",
        "b5cab49196cae130d135ac737db881675aaf8d9178bfbf29c640d9136c7b0cd0",
    ),
    Artifact(
        "nautiles_swg_ca.crt",
        BASE + "egress-gw-spanner/ca/v0.2/nautiles_swg_ca.crt",
        "4382cb2df1b57872b03779dbab0781f3c26af52684f4f6957ffd4c55369ac70e",
    ),
    Artifact(
        "arca_envd",
        BASE + "sandbox-service/misc/arca_envd/latest/arca_envd",
        "47fd9c0b446597f855de653e43ec5d1adf412a303c62d0b9176a0e55eed3e514",
    ),
]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cache", type=Path, help="Previously downloaded, hash-verified files")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    target = args.output / "arca"
    target.mkdir()
    http = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    for artifact in ARTIFACTS:
        cached = args.cache / artifact.name if args.cache else None
        if cached is not None and cached.is_file():
            data = cached.read_bytes()
        else:
            with http.open(artifact.url, timeout=180) as response:
                data = response.read()
        if hashlib.sha256(data).hexdigest() != artifact.sha256:
            raise ValueError(f"SHA-256 mismatch: {artifact.name}")
        (target / artifact.name).write_bytes(data)
    dockerfile = Path(__file__).resolve().parents[1] / "containers/verilogeval.Dockerfile"
    shutil.copy2(dockerfile, args.output / "Dockerfile")
    (args.output / "arca-inputs.json").write_text(
        json.dumps([asdict(artifact) for artifact in ARTIFACTS], indent=2) + "\n"
    )
    (args.output / ".dockerignore").write_text("*\n!Dockerfile\n!arca/\n!arca/**\n")
    print(args.output.resolve())


if __name__ == "__main__":
    main()
