#!/usr/bin/env python3
"""Retired compatibility entry point for the pre-canonical mining inspector."""

raise SystemExit(
    "hourly_mining_inspector.py is retired: it depended on obsolete JSON state "
    "and pre-canonical tokenomics. Use the node /info API, the pool /status API, "
    "and deploy/capture_node_readiness.py instead."
)
