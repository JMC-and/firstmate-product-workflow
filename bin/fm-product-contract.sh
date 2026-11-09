#!/usr/bin/env bash
# Private product-contract entry point. All commands, schemas, transactions and
# predicates are owned by fm-product-contract.py; --help documents the interface.
set -euo pipefail
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
export PYTHONDONTWRITEBYTECODE=1
exec python3 "$SCRIPT_DIR/fm-product-contract.py" "$@"
