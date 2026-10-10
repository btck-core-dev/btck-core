#!/usr/bin/env bash
set -euo pipefail

echo "BLOCKED: the legacy seed installer copied test secrets and exposed unauthenticated RPC." >&2
echo "Follow docs_internal_confidential/BTCK_DEVNET_IGNITION_RUNBOOK_2026-09-28.md instead." >&2
exit 1
