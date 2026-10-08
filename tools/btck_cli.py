#!/usr/bin/env python3
"""Fail-closed node-backed BTC-K wallet CLI.

Every usable network identity is embedded at build time.  The private guardian
MAINNET identity is intentionally versioned separately from the future public
checkpoint identity, so this wallet will reject that later identity until a
new public wallet release is built.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict
import getpass
import hashlib
import json
import os
import sys
import time
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR / "src"))

WALLET_VERSION = "3.2.2-guardian1"
RECOVERY_SCHEME = "bip39-btck-dksap-v1"
DEFAULT_SYNC_URL = "https://wallet.bitcoin-k.com/api/wallet/sync"

APPROVED_NETWORKS = {
    "mainnet": {
        "name": "mainnet",
        "chain_id": "btck-mainnet-v1",
        "genesis_hash": "b9249be0f1b57021a9cfef8241a499c6e975a91e24d6a3fc22ce850000000000",
        "consensus_fingerprint": "209a94078fd965783c3e0162f9744b1ccefddae11149e5102200c0fce4e2865a",
    },
    "devnet": {
        "name": "devnet",
        "chain_id": "btck-devnet-v3",
        "genesis_hash": "00afe7ba34ab52100a7e086f8bf16cee97dfaa80eaf7bc157a8d933d6ed6fa2a",
        "consensus_fingerprint": "68766c59bc22f49956588e2f8a53d6670caa9e8b2801aa6bbb517beb394ddcab",
    },
    "testnet": {
        "name": "testnet",
        "chain_id": "btck-testnet-v3",
        "genesis_hash": "773ef89465a3f3e9eafa7a759740f7ee9f9046039b351af80f54518296f5b747",
        "consensus_fingerprint": "69d8e3337eba35f0ff5ff921a66b0b2506c0d38f4acc6c900de3276846972a97",
    },
}

from privacy.stealth_address import StealthKeyPair
from privacy.bip39 import WORDLIST, derive_btck_stealth_wallet, generate_mnemonic, mnemonic_to_seed, validate_mnemonic
from privacy.wallet_crypto import WALLET_FORMAT, decrypt_wallet_payload, encrypt_wallet_payload
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


def validate_network_identity(network: dict) -> dict:
    if not isinstance(network, dict):
        raise CLIError("Network identity must be an object")
    actual = {
        key: network.get(key)
        for key in ("name", "chain_id", "genesis_hash", "consensus_fingerprint")
    }
    expected = APPROVED_NETWORKS.get(str(actual["name"]))
    if expected is None or actual != expected:
        raise CLIError("Network identity is unknown or not approved by this wallet release")
    return actual


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


def _wallet_from_mnemonic(mnemonic: str, name: str, account_index: int = 0) -> StealthWallet:
    normalized = " ".join(mnemonic.strip().lower().split())
    if not validate_mnemonic(normalized):
        raise CLIError("Mnemonic is not a valid BIP-39 recovery phrase")
    _view, _spend, keypair = derive_btck_stealth_wallet(
        mnemonic_to_seed(normalized), account_index=account_index,
    )
    return StealthWallet(keypair=keypair, name=name)


def _encrypted_recoverable_wallet(
    wallet: StealthWallet,
    password: str,
    *,
    word_count: int,
    account_index: int = 0,
) -> dict:
    envelope = wallet.export_encrypted(password)
    envelope["recovery"] = {
        "scheme": RECOVERY_SCHEME,
        "word_count": int(word_count),
        "account_index": int(account_index),
        "mnemonic_stored": False,
    }
    return envelope


def _read_mnemonic_file(path: str) -> str:
    mnemonic_path = Path(path)
    try:
        if mnemonic_path.stat().st_mode & 0o077:
            raise CLIError("Mnemonic file must not be accessible by group or other users")
        return mnemonic_path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise CLIError(f"Cannot read mnemonic file: {exc}") from exc


def _prompt_visible_mnemonic(word_count: int) -> str:
    valid_words = set(WORDLIST)
    print("Recovery words will be visible while you type. Ensure nobody can see or record your screen.")
    words = []
    for index in range(word_count):
        while True:
            word = input(f"Enter recovery word {index + 1:02d}/{word_count}: ").strip().lower()
            if word in valid_words:
                words.append(word)
                break
            print("That word is not in the English BIP-39 word list. Check the spelling and try again.")
    return " ".join(words)


def _prompt_mnemonic(args, *, word_count: int) -> str:
    if getattr(args, "mnemonic_file", None):
        return _read_mnemonic_file(args.mnemonic_file)
    return _prompt_visible_mnemonic(word_count)


def _print_recovery_sheet(name: str, address: str, mnemonic: str) -> None:
    words = mnemonic.split()
    print("\n" + "=" * 72)
    print(f"WRITE DOWN NOW — {name}")
    print(f"Address: {address}")
    for offset in range(0, len(words), 4):
        print("  " + "   ".join(
            f"{index + 1:02d}. {words[index]}"
            for index in range(offset, min(offset + 4, len(words)))
        ))
    print("The recovery words are NOT stored in the wallet file and will not be shown again.")
    print("=" * 72)


def _confirm_recovery(
    wallet: StealthWallet,
    account_index: int = 0,
    word_count: int = 24,
) -> None:
    print("Re-enter every recovery word in order to verify your written backup.")
    repeated = _prompt_visible_mnemonic(word_count)
    recovered = _wallet_from_mnemonic(repeated, wallet.name, account_index=account_index)
    if not secrets_compare(wallet.address, recovered.address):
        raise CLIError("The recovery phrase does not restore this wallet; do not fund it")
    print("Full recovery verification passed.")


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
    wallet = StealthWallet.load_from_json(str(wallet_path), password=password)
    wallet._source_path = wallet_path
    wallet._unlock_password = password
    return wallet


def _sync_cache_path(wallet: StealthWallet) -> Path | None:
    source = getattr(wallet, "_source_path", None)
    return Path(str(source) + ".sync.json") if source else None


def _load_sync_cache(wallet: StealthWallet) -> dict:
    path = _sync_cache_path(wallet)
    password = getattr(wallet, "_unlock_password", None)
    if path is None or password is None or not path.exists():
        return {"next_height": 0, "anchor_hash": "", "network": None}
    try:
        envelope = read_json(str(path), "wallet sync cache")
        private, public = decrypt_wallet_payload(envelope, password)
        if public.get("format") != "btck-wallet-sync-cache-v1":
            raise ValueError("unsupported cache format")
        if public.get("wallet_address") != wallet.address:
            raise ValueError("cache belongs to a different wallet")
        wallet.utxos = [UTXO(**item) for item in private.get("utxos", [])]
        return {
            "next_height": int(private.get("next_height", 0)),
            "anchor_hash": str(private.get("anchor_hash", "")),
            "network": private.get("network"),
        }
    except (OSError, TypeError, ValueError, KeyError) as exc:
        raise CLIError(f"Encrypted wallet sync cache is unreadable: {exc}") from exc


def _save_sync_cache(wallet: StealthWallet, state: dict) -> None:
    path = _sync_cache_path(wallet)
    password = getattr(wallet, "_unlock_password", None)
    if path is None or password is None:
        return
    private = {
        "next_height": int(state["next_height"]),
        "anchor_hash": str(state["anchor_hash"]),
        "network": state["network"],
        "utxos": [asdict(utxo) for utxo in wallet.utxos],
    }
    public = {"format": "btck-wallet-sync-cache-v1", "wallet_address": wallet.address}
    envelope = encrypt_wallet_payload(private, public, password)
    temporary = path.with_name(f"{path.name}.tmp-{os.getpid()}")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(envelope, handle, separators=(",", ":"), sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _fetch_sync_page(args, state: dict) -> dict:
    sync_url = str(getattr(args, "sync_url", DEFAULT_SYNC_URL))
    parsed = urlparse(sync_url)
    if parsed.scheme != "https" and not (
        parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost", "::1"}
    ):
        raise CLIError("Wallet sync requires HTTPS (plain HTTP is allowed only on loopback)")
    query = urlencode({
        "from_height": int(state["next_height"]),
        "limit": int(getattr(args, "sync_page_size", 250)),
        "anchor_hash": str(state["anchor_hash"]),
    })
    request = Request(f"{sync_url}?{query}", headers={"User-Agent": f"BTC-K-Wallet/{WALLET_VERSION}"})
    try:
        with urlopen(request, timeout=float(getattr(args, "timeout", 10.0))) as response:
            result = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        try:
            detail = json.loads(exc.read().decode("utf-8")).get("error", str(exc))
        except Exception:
            detail = str(exc)
        raise CLIError(f"Wallet sync request failed: {detail}") from exc
    except (URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise CLIError(f"Wallet sync request failed: {exc}") from exc
    if not isinstance(result, dict) or result.get("format") != "btck-wallet-sync-v1":
        raise CLIError("Wallet sync endpoint returned an invalid response")
    return result


def scan_wallet(args, wallet: StealthWallet):
    state = _load_sync_cache(wallet)
    reset_attempted = False
    while True:
        print(
            f"Syncing chain data from height {state['next_height']}...",
            file=sys.stderr, flush=True,
        )
        try:
            page = _fetch_sync_page(args, state)
        except CLIError as exc:
            if (
                not reset_attempted
                and int(state["next_height"]) > 0
                and ("stale" in str(exc).lower() or "outside the canonical" in str(exc).lower())
            ):
                print("Chain cursor changed; safely resyncing from genesis...", file=sys.stderr, flush=True)
                wallet.utxos = []
                state = {"next_height": 0, "anchor_hash": "", "network": None}
                _save_sync_cache(wallet, state)
                reset_attempted = True
                continue
            raise
        network = validate_network_identity(page.get("network", {}))
        if state["network"] is not None and state["network"] != network:
            raise CLIError("Wallet sync cache network identity does not match the endpoint")
        if int(page.get("from_height", -1)) != int(state["next_height"]):
            raise CLIError("Wallet sync endpoint returned a discontinuous page")

        expected_height = int(state["next_height"])
        previous_hash = str(state["anchor_hash"])
        for block in page.get("blocks", []):
            if int(block.get("height", -1)) != expected_height:
                raise CLIError("Wallet sync endpoint returned non-contiguous block heights")
            block_hash = str(block.get("hash", ""))
            if len(block_hash) != 64:
                raise CLIError("Wallet sync endpoint returned an invalid block hash")
            spent = {(str(item["txid"]), int(item["vout"])) for item in block.get("spent", [])}
            if spent:
                wallet.utxos = [utxo for utxo in wallet.utxos if (utxo.txid, utxo.vout) not in spent]
            wallet.scan_chain_outputs(block.get("created", []))
            expected_height += 1
            previous_hash = block_hash

        if int(page.get("next_height", -1)) != expected_height:
            raise CLIError("Wallet sync endpoint returned an invalid next-height cursor")
        if str(page.get("anchor_hash", "")) != previous_hash:
            raise CLIError("Wallet sync endpoint returned an invalid anchor hash")

        state = {
            "next_height": int(page["next_height"]),
            "anchor_hash": str(page["anchor_hash"]),
            "network": network,
        }
        _save_sync_cache(wallet, state)
        tip_height = int(page["tip_height"])
        completed = min(state["next_height"], tip_height + 1)
        percent = 100.0 if tip_height < 0 else completed * 100.0 / (tip_height + 1)
        print(
            f"Sync progress: {completed}/{tip_height + 1} ({percent:.1f}%); "
            f"{len(wallet.utxos)} unspent outputs identified",
            file=sys.stderr, flush=True,
        )
        if page.get("done"):
            return {"selected_height": tip_height}, network


def cmd_new(args):
    words = int(getattr(args, "words", 24))
    mnemonic = generate_mnemonic(256 if words == 24 else 128)
    wallet = _wallet_from_mnemonic(mnemonic, args.name)
    output = Path(args.output or f"{args.name}_wallet.json")
    write_private_json(
        output,
        _encrypted_recoverable_wallet(
            wallet, wallet_password(args, confirm=True), word_count=words,
        ),
    )
    print(f"Encrypted wallet created: {output} (mode 600)")
    print(f"Address: {wallet.address}")
    _print_recovery_sheet(args.name, wallet.address, mnemonic)
    if not getattr(args, "skip_immediate_recovery_check", False):
        _confirm_recovery(wallet, word_count=words)


def cmd_restore(args):
    mnemonic = " ".join(
        _prompt_mnemonic(args, word_count=int(args.words)).strip().lower().split()
    )
    words = len(mnemonic.split())
    wallet = _wallet_from_mnemonic(mnemonic, args.name, account_index=args.account_index)
    output = Path(args.output)
    write_private_json(
        output,
        _encrypted_recoverable_wallet(
            wallet,
            wallet_password(args, confirm=True),
            word_count=words,
            account_index=args.account_index,
        ),
    )
    print(f"Wallet restored to: {output} (mode 600)")
    print(f"Address: {wallet.address}")


def cmd_verify_recovery(args):
    wallet = load_wallet(args.wallet, args)
    raw = read_json(args.wallet, "wallet file")
    recovery = raw.get("recovery", {})
    if recovery.get("scheme") != RECOVERY_SCHEME:
        raise CLIError("Wallet does not declare the supported mnemonic recovery scheme")
    account_index = int(recovery.get("account_index", 0))
    recovered = _wallet_from_mnemonic(
        _prompt_mnemonic(
            args, word_count=int(recovery.get("word_count", 24)),
        ),
        wallet.name,
        account_index=account_index,
    )
    if not secrets_compare(wallet.address, recovered.address):
        raise CLIError("Recovery phrase does not reproduce this wallet address")
    print("Recovery verified: mnemonic and encrypted wallet produce the same address.")
    print(f"Address: {wallet.address}")


def cmd_eco_output(args):
    wallet = load_wallet(args.wallet, args)
    r_ephem, p_onetime = StealthKeyPair.generate_one_time_stealth_output(wallet.address)
    if not wallet.keypair.is_my_stealth_output(r_ephem, p_onetime):
        raise CLIError("Internal error: generated ecosystem output is not recoverable by this wallet")
    payload = {
        "format": "btck-mainnet-eco-output-v1",
        "wallet_address": wallet.address,
        "eco_fund_output": [r_ephem.hex(), p_onetime.hex()],
        "field_order": ["R_ephem", "P_onetime"],
    }
    write_private_json(Path(args.out), payload)
    print(f"Verified public ecosystem output written: {args.out}")
    print(json.dumps(payload, indent=2))


def cmd_init_mainnet_wallets(args):
    destination = Path(args.output_dir).expanduser()
    destination.mkdir(mode=0o700, parents=True, exist_ok=True)
    if destination.stat().st_mode & 0o077:
        raise CLIError("Output directory must not be accessible by group or other users")
    roles = (
        ("personal", "BTC-K Mainnet Personal / Mining"),
        ("pool_treasury", "BTC-K Mainnet Pool Treasury"),
        ("eco_fund", "BTC-K Mainnet Ecosystem Fund"),
    )
    targets = {role: destination / f"{role}_wallet.json" for role, _name in roles}
    targets["public"] = destination / "mainnet_wallet_public.json"
    if targets["public"].exists():
        raise CLIError(
            "Mainnet public wallet parameters already exist; refusing to overwrite the established identities"
        )
    existing_wallets = [targets[role] for role, _name in roles if targets[role].exists()]
    if existing_wallets and not getattr(args, "resume", False):
        raise CLIError(
            "An incomplete wallet setup was found; use --resume to verify the existing wallets and continue"
        )

    created: dict[str, tuple[StealthWallet, str]] = {}
    word_count = int(args.words)
    for role, name in roles:
        if targets[role].exists():
            print(f"\nExisting wallet found: {name}")
            existing_wallet = load_wallet(str(targets[role]), args)
            raw = read_json(str(targets[role]), "wallet file")
            recovery = raw.get("recovery", {})
            if recovery.get("scheme") != RECOVERY_SCHEME:
                raise CLIError(f"Existing {role} wallet has no supported recovery metadata")
            existing_word_count = int(recovery.get("word_count", word_count))
            existing_account_index = int(recovery.get("account_index", 0))
            _confirm_recovery(
                existing_wallet,
                account_index=existing_account_index,
                word_count=existing_word_count,
            )
            created[role] = (existing_wallet, "")
            continue
        print(f"\nCreate password for {name} (minimum 10 characters).")
        password = wallet_password(args, confirm=True)
        mnemonic = generate_mnemonic(256 if word_count == 24 else 128)
        wallet = _wallet_from_mnemonic(mnemonic, name)
        write_private_json(
            targets[role],
            _encrypted_recoverable_wallet(
                wallet, password, word_count=word_count,
            ),
        )
        restored = StealthWallet.load_from_json(str(targets[role]), password=password)
        if restored.address != wallet.address:
            raise CLIError(f"Encrypted round-trip verification failed for {role}")
        created[role] = (wallet, mnemonic)
        _print_recovery_sheet(name, wallet.address, mnemonic)
        if not getattr(args, "skip_immediate_recovery_check", False):
            _confirm_recovery(wallet, word_count=word_count)

    eco_wallet = created["eco_fund"][0]
    r_ephem, p_onetime = StealthKeyPair.generate_one_time_stealth_output(eco_wallet.address)
    if not eco_wallet.keypair.is_my_stealth_output(r_ephem, p_onetime):
        raise CLIError("Generated ecosystem output failed ownership verification")
    public = {
        "format": "btck-mainnet-wallet-public-v1",
        "personal_address": created["personal"][0].address,
        "pool_treasury_address": created["pool_treasury"][0].address,
        "eco_fund_address": eco_wallet.address,
        "eco_fund_output": [r_ephem.hex(), p_onetime.hex()],
        "eco_fund_output_order": ["R_ephem", "P_onetime"],
    }
    write_private_json(targets["public"], public)
    print(f"\nThree encrypted wallets and verified public parameters created in: {destination}")
    print("Before funding or genesis, run verify-recovery separately for all three wallets.")


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
    snapshot, _network = scan_wallet(args, wallet)
    print(f"Network height: {snapshot['selected_height']}")
    print(f"Address: {wallet.address}")
    height = int(snapshot["selected_height"])
    spendable_satk = wallet.get_spendable_balance_satk(height)
    print(f"Tracked outputs: {len(wallet.utxos)}")
    print(f"Tracked balance: {wallet.get_balance_satk()} SatK ({wallet.get_balance_btck():.8f} BTC-K)")
    print(f"Spendable balance: {spendable_satk} SatK ({spendable_satk / SATK_PER_COIN:.8f} BTC-K)")


def cmd_transfer(args):
    if args.offline:
        raise CLIError("Legacy --offline is disabled; use export-watch, prepare, sign and broadcast")
    wallet = load_wallet(args.wallet, args)
    snapshot, _network = scan_wallet(args, wallet)
    tx = wallet.create_and_sign_transfer(
        recipient_stealth_addr=args.to,
        amount_satk=amount_to_satk(args.amount),
        fee_satk=amount_to_satk(args.fee),
        current_height=int(snapshot["selected_height"]),
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
    snapshot, network = scan_wallet(args, wallet)
    amount_satk = amount_to_satk(args.amount)
    fee_satk = amount_to_satk(args.fee)
    needed = amount_satk + fee_satk
    selected = []
    selected_total = 0
    for utxo in wallet.utxos:
        if not wallet.is_utxo_spendable(utxo, int(snapshot["selected_height"])):
            continue
        selected.append({
            "txid": utxo.txid,
            "vout": utxo.vout,
            "amount_satk": utxo.amount_satk,
            "R_ephem_hex": utxo.R_ephem_hex,
            "P_onetime_hex": utxo.P_onetime_hex,
            "block_height": utxo.block_height,
            "is_coinbase": utxo.is_coinbase,
            "timelock_until_height": utxo.timelock_until_height,
            "kind": utxo.kind,
        })
        selected_total += utxo.amount_satk
        if selected_total >= needed:
            break
    if selected_total < needed:
        raise CLIError(f"Insufficient balance: have {selected_total} SatK, need {needed} SatK")
    package = {
        "format": "btck-unsigned-transfer-v1",
        "created_at": int(time.time()),
        "network": network,
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
    validate_network_identity(package.get("network"))
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
            is_coinbase=bool(raw.get("is_coinbase", False)),
            timelock_until_height=int(raw.get("timelock_until_height", 0) or 0),
            kind=str(raw.get("kind", "standard")),
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
    if payload.get("format") != "btck-signed-transfer-v1":
        raise CLIError("Unbound raw transaction refused; broadcast a signed transfer package")
    expected = validate_network_identity(payload.get("network", {}))
    actual = get_approved_node_identity(args)
    if expected != actual:
        raise CLIError("Signed transaction package is bound to a different network")
    tx = transaction_from_dict(payload["transaction"])
    raw = transaction_to_dict(tx)
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
    parser.add_argument(
        "--sync-url",
        default=os.getenv("BTCK_SYNC_URL", DEFAULT_SYNC_URL),
        help="Public HTTPS endpoint used for secret-free incremental chain synchronization",
    )
    parser.add_argument("--sync-page-size", type=int, default=250)
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--password-file", help="Read wallet password from a permission-restricted file")
    parser.add_argument(
        "--allow-legacy-plaintext", action="store_true",
        help="Explicitly allow an old plaintext wallet (migration/emergency DEVNET use only)",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    new = commands.add_parser("new", help="Create a recoverable encrypted wallet")
    new.add_argument("--name", default="my_wallet")
    new.add_argument("-o", "--output")
    new.add_argument("--words", type=int, choices=(12, 24), default=24)
    new.add_argument(
        "--skip-immediate-recovery-check", action="store_true",
        help="automation only; create without immediately re-entering the words",
    )
    new.set_defaults(func=cmd_new)

    restore = commands.add_parser("restore", help="Restore an encrypted wallet from BIP-39 words")
    restore.add_argument("--name", default="restored_wallet")
    restore.add_argument("-o", "--output", required=True)
    restore.add_argument("--account-index", type=int, default=0)
    restore.add_argument("--words", type=int, choices=(12, 24), default=24)
    restore.add_argument("--mnemonic-file", help="Read words from a mode-600 file instead of visible word-by-word input")
    restore.set_defaults(func=cmd_restore)

    verify = commands.add_parser("verify-recovery", help="Prove recovery words reproduce a wallet address")
    verify.add_argument("wallet")
    verify.add_argument("--mnemonic-file", help="Read words from a mode-600 file instead of visible word-by-word input")
    verify.set_defaults(func=cmd_verify_recovery)

    eco_output = commands.add_parser(
        "eco-output", help="Create a verified public (R_ephem, P_onetime) ecosystem output",
    )
    eco_output.add_argument("wallet")
    eco_output.add_argument("--out", required=True)
    eco_output.set_defaults(func=cmd_eco_output)

    init_wallets = commands.add_parser(
        "init-mainnet-wallets", help="Create independent personal, pool and ecosystem wallets",
    )
    init_wallets.add_argument("--output-dir", required=True)
    init_wallets.add_argument("--words", type=int, choices=(12, 24), default=24)
    init_wallets.add_argument(
        "--resume", action="store_true",
        help="verify existing partial wallet files and continue without overwriting",
    )
    init_wallets.add_argument(
        "--skip-immediate-recovery-check", action="store_true",
        help="automation only; create without immediately re-entering each phrase",
    )
    init_wallets.set_defaults(func=cmd_init_mainnet_wallets)

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
