#!/usr/bin/env python3
"""Minimal node-backed BTC-K wallet CLI for devnet/testnet."""

from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import os
import sys
import time
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR / "src"))

WALLET_VERSION = "3.1.0-rc1"

from privacy.stealth_address import StealthKeyPair
from privacy.wallet_crypto import WALLET_FORMAT
from privacy.wallet_engine import SATK_PER_COIN, StealthWallet, UTXO, transaction_from_dict, transaction_to_dict


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


def read_json(path: str, description: str) -> dict:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CLIError(f"Cannot read {description}: {exc}") from exc
    if not isinstance(payload, dict):
        raise CLIError(f"{description.capitalize()} must contain a JSON object")
    return payload


def write_private_json(path: Path, payload: dict) -> None:
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise CLIError(f"Refusing to overwrite existing file: {path}") from exc
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")


def wallet_password(args, *, confirm: bool = False) -> str:
    if args.password_file:
        try:
            password_path = Path(args.password_file)
            if password_path.stat().st_mode & 0o077:
                raise CLIError("Password file must not be accessible by group or other users")
            password = password_path.read_text(encoding="utf-8").rstrip("\r\n")
        except OSError as exc:
            raise CLIError(f"Cannot read password file: {exc}") from exc
    else:
        password = getpass.getpass("Wallet password: ")
        if confirm:
            repeated = getpass.getpass("Confirm wallet password: ")
            if not secrets_compare(password, repeated):
                raise CLIError("Wallet passwords do not match")
    if len(password) < 10:
        raise CLIError("Wallet password must contain at least 10 characters")
    return password


def secrets_compare(left: str, right: str) -> bool:
    import hmac
    return hmac.compare_digest(left.encode("utf-8"), right.encode("utf-8"))


def load_wallet(path: str, args) -> StealthWallet:
    wallet_path = Path(path)
    if not wallet_path.is_file():
        raise CLIError(f"Wallet file not found: {wallet_path}")
    raw = read_json(str(wallet_path), "wallet file")
    if raw.get("format") == WALLET_FORMAT:
        password = wallet_password(args)
    elif getattr(args, "allow_legacy_plaintext", False):
        password = None
    else:
        raise CLIError("Legacy plaintext wallet refused; run migrate-encrypt on an offline host")
    return StealthWallet.load_from_json(str(wallet_path), password=password)


def scan_wallet(args, wallet: StealthWallet):
    snapshot = rpc_call(args, "getUTXOSet")
    wallet.scan_chain_outputs(snapshot["outputs"])
    return snapshot


def cmd_new(args):
    wallet = StealthWallet.generate(name=args.name)
    output = Path(args.output or f"{args.name}_wallet.json")
    write_private_json(output, wallet.export_encrypted(wallet_password(args, confirm=True)))
    print(f"Encrypted wallet created: {output} (mode 600)")
    print(f"Address: {wallet.address}")
    print("Back up this file offline. Private keys were not printed.")


def cmd_info(args):
    wallet = load_wallet(args.wallet, args)
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
    wallet = load_wallet(args.wallet, args)
    snapshot = scan_wallet(args, wallet)
    print(f"Network height: {snapshot['selected_height']}")
    print(f"Address: {wallet.address}")
    print(f"Spendable/tracked outputs: {len(wallet.utxos)}")
    print(f"Balance: {wallet.get_balance_satk()} SatK ({wallet.get_balance_btck():.8f} BTC-K)")


def cmd_transfer(args):
    if args.offline:
        raise CLIError("Legacy --offline is disabled; use export-watch, prepare, sign and broadcast")
    wallet = load_wallet(args.wallet, args)
    scan_wallet(args, wallet)
    tx = wallet.create_and_sign_transfer(
        recipient_stealth_addr=args.to,
        amount_satk=amount_to_satk(args.amount),
        fee_satk=amount_to_satk(args.fee),
    )
    raw = transaction_to_dict(tx)
    result = rpc_call(args, "submitTransaction", {"transaction": raw})
    if not result.get("accepted"):
        raise CLIError("Node did not accept the transaction")
    print(f"Accepted by node: {result['txid']}")
    print(f"Status: {result['status']}")
    print(f"Fee: {result.get('fee_satk', 0)} SatK")


def cmd_migrate_encrypt(args):
    raw = read_json(args.wallet, "wallet file")
    if raw.get("format") == WALLET_FORMAT:
        raise CLIError("Wallet is already encrypted")
    wallet = StealthWallet.load_from_json(args.wallet)
    output = Path(args.out)
    write_private_json(output, wallet.export_encrypted(wallet_password(args, confirm=True)))
    print(f"Encrypted wallet written: {output} (original was not modified)")


def cmd_export_watch(args):
    wallet = load_wallet(args.wallet, args)
    payload = {
        "format": "btck-watch-wallet-v1",
        "name": wallet.name,
        "address": wallet.address,
        "spend_pub_hex": wallet.keypair.spend_pub_bytes.hex(),
        "view_priv_hex": f"{wallet.keypair.view_priv:064x}",
        "view_pub_hex": wallet.keypair.view_pub_bytes.hex(),
    }
    write_private_json(Path(args.out), payload)
    print(f"Watch-only wallet written: {args.out} (contains the private view key, never the spend key)")


def _load_watch_wallet(path: str) -> StealthWallet:
    raw = read_json(path, "watch-only wallet")
    if raw.get("format") != "btck-watch-wallet-v1":
        raise CLIError("Unsupported watch-only wallet format")
    try:
        keypair = StealthKeyPair(
            spend_priv_scalar=None,
            view_priv_scalar=int(raw["view_priv_hex"], 16),
            spend_pub_bytes=bytes.fromhex(raw["spend_pub_hex"]),
        )
        wallet = StealthWallet(keypair=keypair, name=raw.get("name", "watch-only"))
    except (KeyError, TypeError, ValueError) as exc:
        raise CLIError(f"Malformed watch-only wallet: {exc}") from exc
    if wallet.address != raw.get("address"):
        raise CLIError("Watch-only wallet address does not match its keys")
    return wallet


def _package_id(payload: dict) -> str:
    unsigned = {key: value for key, value in payload.items() if key != "package_id"}
    encoded = json.dumps(unsigned, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def cmd_prepare(args):
    wallet = _load_watch_wallet(args.watch_wallet)
    snapshot = scan_wallet(args, wallet)
    amount_satk = amount_to_satk(args.amount)
    fee_satk = amount_to_satk(args.fee)
    needed = amount_satk + fee_satk
    selected = []
    selected_total = 0
    for utxo in wallet.utxos:
        selected.append({
            "txid": utxo.txid,
            "vout": utxo.vout,
            "amount_satk": utxo.amount_satk,
            "R_ephem_hex": utxo.R_ephem_hex,
            "P_onetime_hex": utxo.P_onetime_hex,
            "block_height": utxo.block_height,
        })
        selected_total += utxo.amount_satk
        if selected_total >= needed:
            break
    if selected_total < needed:
        raise CLIError(f"Insufficient balance: have {selected_total} SatK, need {needed} SatK")
    dag = rpc_call(args, "getBlockDagInfo")
    package = {
        "format": "btck-unsigned-transfer-v1",
        "created_at": int(time.time()),
        "network": {
            "name": dag["network"],
            "chain_id": dag["chain_id"],
            "genesis_hash": dag["genesis_hash"],
        },
        "selected_height": int(snapshot["selected_height"]),
        "sender_address": wallet.address,
        "recipient_address": args.to,
        "amount_satk": amount_satk,
        "fee_satk": fee_satk,
        "inputs": selected,
    }
    package["package_id"] = _package_id(package)
    write_private_json(Path(args.out), package)
    print(f"Unsigned transfer package written: {args.out}")
    print(f"Package ID: {package['package_id']}")


def cmd_sign(args):
    package = read_json(args.package, "unsigned transfer package")
    if package.get("format") != "btck-unsigned-transfer-v1" or package.get("package_id") != _package_id(package):
        raise CLIError("Unsigned transfer package format or digest is invalid")
    print(f"Package ID: {package['package_id']}")
    print(f"Network: {package.get('network')}")
    print(f"Recipient: {package.get('recipient_address')}")
    print(f"Amount: {package.get('amount_satk')} SatK")
    print(f"Fee: {package.get('fee_satk')} SatK")
    print(f"Inputs: {len(package.get('inputs', []))}")
    if args.approve_package_id != package["package_id"]:
        raise CLIError("Review the package details, then pass its exact ID with --approve-package-id")
    wallet = load_wallet(args.wallet, args)
    if package.get("sender_address") != wallet.address:
        raise CLIError("Unsigned transfer package belongs to another wallet")
    inputs = []
    seen = set()
    for raw in package.get("inputs", []):
        outpoint = (str(raw["txid"]), int(raw["vout"]))
        if outpoint in seen:
            raise CLIError("Unsigned transfer package contains duplicate inputs")
        seen.add(outpoint)
        R = bytes.fromhex(raw["R_ephem_hex"])
        P = bytes.fromhex(raw["P_onetime_hex"])
        if not wallet.keypair.is_my_stealth_output(R, P):
            raise CLIError(f"Input {outpoint} does not belong to this wallet")
        inputs.append(UTXO(
            txid=outpoint[0], vout=outpoint[1], amount_satk=int(raw["amount_satk"]),
            R_ephem_hex=raw["R_ephem_hex"], P_onetime_hex=raw["P_onetime_hex"],
            block_height=int(raw.get("block_height", 0)),
        ))
    if not inputs:
        raise CLIError("Unsigned transfer package has no inputs")
    amount_satk = int(package["amount_satk"])
    fee_satk = int(package["fee_satk"])
    if amount_satk <= 0 or fee_satk < 0:
        raise CLIError("Unsigned transfer amount or fee is invalid")
    wallet.utxos = inputs
    tx = wallet.create_and_sign_transfer(package["recipient_address"], amount_satk, fee_satk)
    if {(item.prev_txid, item.prev_vout) for item in tx.inputs} != seen:
        raise CLIError("Signer did not consume exactly the reviewed package inputs")
    signed = {
        "format": "btck-signed-transfer-v1",
        "package_id": package["package_id"],
        "network": package["network"],
        "transaction": transaction_to_dict(tx),
    }
    write_private_json(Path(args.out), signed)
    print(f"Signed transaction written without network access: {args.out}")
    print(f"Transaction ID: {tx.txid}")


def cmd_broadcast(args):
    payload = read_json(args.transaction, "signed transaction")
    if payload.get("format") == "btck-signed-transfer-v1":
        network = rpc_call(args, "getBlockDagInfo")
        expected = payload.get("network", {})
        actual = {key: network.get(key) for key in ("name", "chain_id", "genesis_hash")}
        if expected != actual:
            raise CLIError("Signed transaction package is bound to a different network")
        tx = transaction_from_dict(payload["transaction"])
        raw = transaction_to_dict(tx)
    else:
        raw = transaction_to_dict(transaction_from_dict(payload))
    result = rpc_call(args, "submitTransaction", {"transaction": raw})
    print(json.dumps(result, indent=2))


def cmd_status(args):
    result = rpc_call(args, "getTransaction", {"txid": args.txid})
    print(json.dumps(result, indent=2))


def build_parser():
    parser = argparse.ArgumentParser(description="BTC-K node-backed wallet CLI")
    parser.add_argument("--version", action="version", version=f"BTC-K Wallet {WALLET_VERSION}")
    parser.add_argument("--rpc-url", default=os.getenv("BTCK_RPC_URL", "http://127.0.0.1:16110/rpc"))
    parser.add_argument("--rpc-token", help="RPC bearer token (prefer BTCK_RPC_TOKEN environment variable)")
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--password-file", help="Read wallet password from a permission-restricted file")
    parser.add_argument(
        "--allow-legacy-plaintext", action="store_true",
        help="Explicitly allow an old plaintext wallet (migration/emergency DEVNET use only)",
    )
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

    migrate = commands.add_parser("migrate-encrypt", help="Create an encrypted copy of a legacy plaintext wallet")
    migrate.add_argument("wallet")
    migrate.add_argument("--out", required=True)
    migrate.set_defaults(func=cmd_migrate_encrypt)

    watch = commands.add_parser("export-watch", help="Export a watch-only wallet for the online preparation host")
    watch.add_argument("wallet")
    watch.add_argument("--out", required=True)
    watch.set_defaults(func=cmd_export_watch)

    prepare = commands.add_parser("prepare", help="Online: prepare an unsigned transfer package")
    prepare.add_argument("watch_wallet")
    prepare.add_argument("--to", required=True)
    prepare.add_argument("--amount", required=True)
    prepare.add_argument("--fee", default="0.0001")
    prepare.add_argument("--out", required=True)
    prepare.set_defaults(func=cmd_prepare)

    sign = commands.add_parser("sign", help="Offline: verify and sign an unsigned transfer package")
    sign.add_argument("wallet")
    sign.add_argument("package")
    sign.add_argument("--out", required=True)
    sign.add_argument("--approve-package-id", required=True, help="Exact reviewed unsigned package ID")
    sign.set_defaults(func=cmd_sign)

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
