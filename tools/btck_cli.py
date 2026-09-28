#!/usr/bin/env python3
"""Minimal node-backed BTC-K wallet CLI for devnet/testnet."""

from __future__ import annotations

import argparse
import json
import os
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR / "src"))

from privacy.wallet_engine import SATK_PER_COIN, StealthWallet, transaction_to_dict


class CLIError(RuntimeError):
    pass


def rpc_call(args, method: str, params: dict | None = None):
    payload = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}).encode()
    headers = {"Content-Type": "application/json"}
    token = args.rpc_token or os.getenv("BTCK_RPC_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = Request(args.rpc_url, data=payload, headers=headers, method="POST")
    try:
        with urlopen(request, timeout=args.timeout) as response:
            result = json.loads(response.read().decode())
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise CLIError(f"Node RPC request failed: {exc}") from exc
    if "error" in result:
        raise CLIError(result["error"].get("message", "Node RPC error"))
    return result.get("result")


def amount_to_satk(value: str) -> int:
    try:
        amount = Decimal(value)
    except InvalidOperation as exc:
        raise CLIError("Amount must be a decimal number") from exc
    satk = amount * SATK_PER_COIN
    if amount <= 0 or satk != satk.to_integral_value():
        raise CLIError("Amount must be positive with no more than 8 decimal places")
    return int(satk)


def load_wallet(path: str) -> StealthWallet:
    wallet_path = Path(path)
    if not wallet_path.is_file():
        raise CLIError(f"Wallet file not found: {wallet_path}")
    return StealthWallet.load_from_json(str(wallet_path))


def scan_wallet(args, wallet: StealthWallet):
    snapshot = rpc_call(args, "getUTXOSet")
    wallet.scan_chain_outputs(snapshot["outputs"])
    return snapshot


def cmd_new(args):
    wallet = StealthWallet.generate(name=args.name)
    output = Path(args.output or f"{args.name}_wallet.json")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    try:
        descriptor = os.open(output, flags, 0o600)
    except FileExistsError as exc:
        raise CLIError(f"Refusing to overwrite existing wallet: {output}") from exc
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(wallet.export_to_dict(include_private=True), handle, indent=2)
        handle.write("\n")
    print(f"Wallet created: {output} (mode 600)")
    print(f"Address: {wallet.address}")
    print("Back up this file offline. Private keys were not printed.")


def cmd_info(args):
    wallet = load_wallet(args.wallet)
    print(f"Name: {wallet.name}")
    print(f"Address: {wallet.address}")
    print(f"Spend public key: {wallet.keypair.spend_pub_bytes.hex()}")
    print(f"View public key: {wallet.keypair.view_pub_bytes.hex()}")
    if args.show_private:
        if not args.i_understand:
            raise CLIError("Printing secrets requires --i-understand-private-key-risk")
        print(f"Spend private key: {wallet.keypair.spend_priv:064x}")
        print(f"View private key: {wallet.keypair.view_priv:064x}")


def cmd_balance(args):
    wallet = load_wallet(args.wallet)
    snapshot = scan_wallet(args, wallet)
    print(f"Network height: {snapshot['selected_height']}")
    print(f"Address: {wallet.address}")
    print(f"Spendable/tracked outputs: {len(wallet.utxos)}")
    print(f"Balance: {wallet.get_balance_satk()} SatK ({wallet.get_balance_btck():.8f} BTC-K)")


def cmd_transfer(args):
    wallet = load_wallet(args.wallet)
    scan_wallet(args, wallet)
    tx = wallet.create_and_sign_transfer(
        recipient_stealth_addr=args.to,
        amount_satk=amount_to_satk(args.amount),
        fee_satk=amount_to_satk(args.fee),
    )
    raw = transaction_to_dict(tx)
    if args.offline:
        output = Path(args.out or f"tx_{tx.txid}.json")
        output.write_text(json.dumps(raw, indent=2) + "\n", encoding="utf-8")
        print(f"Signed transaction saved but not broadcast: {output}")
    else:
        result = rpc_call(args, "submitTransaction", {"transaction": raw})
        if not result.get("accepted"):
            raise CLIError("Node did not accept the transaction")
        print(f"Accepted by node: {result['txid']}")
        print(f"Status: {result['status']}")
        print(f"Fee: {result.get('fee_satk', 0)} SatK")


def cmd_broadcast(args):
    try:
        raw = json.loads(Path(args.transaction).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CLIError(f"Cannot read signed transaction: {exc}") from exc
    result = rpc_call(args, "submitTransaction", {"transaction": raw})
    print(json.dumps(result, indent=2))


def cmd_status(args):
    result = rpc_call(args, "getTransaction", {"txid": args.txid})
    print(json.dumps(result, indent=2))


def build_parser():
    parser = argparse.ArgumentParser(description="BTC-K node-backed wallet CLI")
    parser.add_argument("--rpc-url", default=os.getenv("BTCK_RPC_URL", "http://127.0.0.1:16110/rpc"))
    parser.add_argument("--rpc-token", help="RPC bearer token (prefer BTCK_RPC_TOKEN environment variable)")
    parser.add_argument("--timeout", type=float, default=10.0)
    commands = parser.add_subparsers(dest="command", required=True)

    new = commands.add_parser("new", help="Create a wallet file")
    new.add_argument("--name", default="my_wallet")
    new.add_argument("-o", "--output")
    new.set_defaults(func=cmd_new)

    info = commands.add_parser("info", help="Show wallet information")
    info.add_argument("wallet")
    info.add_argument("--show-private", action="store_true")
    info.add_argument("--i-understand-private-key-risk", dest="i_understand", action="store_true")
    info.set_defaults(func=cmd_info)

    balance = commands.add_parser("balance", help="Scan the node UTXO set locally")
    balance.add_argument("wallet")
    balance.set_defaults(func=cmd_balance)

    transfer = commands.add_parser("transfer", help="Create, sign and submit a transfer")
    transfer.add_argument("wallet")
    transfer.add_argument("--to", required=True)
    transfer.add_argument("--amount", required=True, help="BTC-K decimal amount")
    transfer.add_argument("--fee", default="0.0001", help="BTC-K decimal fee")
    transfer.add_argument("--offline", action="store_true", help="Save without broadcasting")
    transfer.add_argument("--out")
    transfer.set_defaults(func=cmd_transfer)

    broadcast = commands.add_parser("broadcast", help="Submit an offline-signed transaction")
    broadcast.add_argument("transaction")
    broadcast.set_defaults(func=cmd_broadcast)

    status = commands.add_parser("status", help="Query mempool/confirmation status")
    status.add_argument("txid")
    status.set_defaults(func=cmd_status)
    return parser


def main():
    args = build_parser().parse_args()
    try:
        args.func(args)
    except (CLIError, ValueError, KeyError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
