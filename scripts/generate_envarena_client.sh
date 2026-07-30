#!/usr/bin/env bash
set -euo pipefail

# Regenerate the vendored OpenAPI SDK.  The thin wrapper in
# areal_rsi/services/envarena/client.py is the business-facing API.
export UV_INDEX_URL="${UV_INDEX_URL:-https://mirrors.aliyun.com/pypi/simple/}"

uvx --from 'openapi-python-client==0.28.0' openapi-python-client generate \
  --path docs/envarena-openapi.yaml \
  --meta none \
  --output-path areal_rsi/services/envarena/generated \
  --overwrite
