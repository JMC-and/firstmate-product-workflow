#!/usr/bin/env bash
# Public CLI regressions; optional real pinned-package proof needs explicit setup authority.
set -euo pipefail
ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
export PYTHONDONTWRITEBYTECODE=1
exec python3 "$ROOT/tests/fm-product-graph.test.py" "$ROOT"
