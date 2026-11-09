#!/usr/bin/env bash
# Public-interface regression suite; private fixtures live only in a temp home.
set -euo pipefail
ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
export PYTHONDONTWRITEBYTECODE=1
exec python3 "$ROOT/tests/fm-product-contract.test.py" "$ROOT"
