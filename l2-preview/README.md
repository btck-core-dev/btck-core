# BTC-K L2 Compute invitation preview

Gateway: https://compute.bitcoin-k.com
Downloads: https://compute.bitcoin-k.com/downloads/

This directory contains the Compute Node, resident Worker, development-credit gateway, customer SDK, build scripts and focused tests, isolated from L1 production code. No mainnet funds are enabled.

Miners connect outward over HTTPS and do not need a domain, public IP or inbound port forwarding. Use an operator-issued private Worker invitation. Customers and Agents use separate credentials. Models and runtimes belong to the miner.

Read docs/L2_COMPUTE_CONNECT_QUICKSTART.md and docs/L2_AGENT_QUICKSTART.md. Run scripts and tests from this directory. Build Windows with packaging/build_compute_connect_windows.ps1 using Python 3.10+ with Tcl/Tk and PyInstaller 6.16.0. Linux and Agent package builders use only the standard library. Run focused tests with python3 -m unittest discover -s tests.

A 35B TP4 model on four RTX 4070 Ti SUPER GPUs and a 27B TP2 model on two RTX 3090 GPUs using an existing WSL2 runtime passed near-16K context checks and public Agent/tool routing. Two-node parallel requests, signed receipts and idempotent charging passed. Other machines, arbitrary models, 4bit KV, speculative decoding, native Windows vLLM and long-term availability are not qualified by these results. Standalone Windows startup installation does not need a separate Python installation for Compute Node; model runtimes remain operator supplied.

The Windows executable uses the same Worker sources in this snapshot. PyInstaller builds are not claimed bit-for-bit reproducible. Release assets include package hashes and source provenance.
