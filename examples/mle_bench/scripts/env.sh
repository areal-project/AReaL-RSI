#!/usr/bin/env bash
# Source this file; paths remain correct when the checkout moves.
MLE_ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)
export MLE_ROOT
export MLE_IMAGE=${MLE_IMAGE:-$MLE_ROOT/images/mlebench-h200.sif}
export MLE_DATA_DIR=${MLE_DATA_DIR:-$MLE_ROOT/data}
export MLE_CACHE_DIR=${MLE_CACHE_DIR:-$MLE_ROOT/cache/runtime}
mkdir -p "$MLE_ROOT"/{logs,runs} "$MLE_DATA_DIR" "$MLE_CACHE_DIR"
