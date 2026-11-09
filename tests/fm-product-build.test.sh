#!/usr/bin/env bash
# B1 public-interface replay through genuine approved private ledger fixtures.
set -euo pipefail
ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
export PYTHONDONTWRITEBYTECODE=1
exec python3 "$ROOT/tests/fm-product-build.test.py" "$ROOT"
