"""Encrypted wallet and genuinely offline signing workflow tests."""

import json
import os
import sys
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError, URLError
from unittest.mock import MagicMock, patch


sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "tools")))

import btck_cli
from privacy.stealth_address import StealthKeyPair
from privacy.bip39 import mnemonic_to_seed, validate_mnemonic
from privacy.wallet_crypto import aead_decrypt, aead_encrypt, encrypt_wallet_payload
from privacy.wallet_engine import StealthWallet, UTXO, transaction_from_dict
from stratum.stratum_server import load_pool_treasury_wallet


DEVNET_IDENTITY = {
    "network": "devnet",
    "chain_id": "btck-devnet-v3",
    "genesis_hash": "00afe7ba34ab52100a7e086f8bf16cee97dfaa80eaf7bc157a8d933d6ed6fa2a",
    "consensus_fingerprint": "68766c59bc22f49956588e2f8a53d6670caa9e8b2801aa6bbb517beb394ddcab",
}


class TestWalletEncryption(unittest.TestCase):
    def test_balance_display_unit_is_explicit_and_does_not_create_an_asset(self):
        parser = btck_cli.build_parser()
        default_args = parser.parse_args(["balance", "wallet.json"])
        satk_args = parser.parse_args(["balance", "wallet.json", "--unit", "satk"])

        self.assertEqual(default_args.unit, "btck")
        self.assertEqual(satk_args.unit, "satk")
        with self.assertRaises(SystemExit):
            parser.parse_args(["balance", "wallet.json", "--unit", "l2-token"])

    def test_bip39_official_seed_vector(self):
        mnemonic = "abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon about"
        expected = (
            "c55257c360c07c72029aebc1b53c05ed0362ada38ead3e3e9efa3708e5349553"
            "1f09a6987599d18264c1e1c92f2cf141630c7a3c4ab7c81b2f001698e7463b04"
        )
        self.assertTrue(validate_mnemonic(mnemonic))
        self.assertEqual(mnemonic_to_seed(mnemonic, "TREZOR").hex(), expected)

    def test_rfc8439_aead_vector(self):
        key = bytes.fromhex("808182838485868788898a8b8c8d8e8f909192939495969798999a9b9c9d9e9f")
        nonce = bytes.fromhex("070000004041424344454647")
        aad = bytes.fromhex("50515253c0c1c2c3c4c5c6c7")
        plaintext = bytes.fromhex(
            "4c616469657320616e642047656e746c656d656e206f662074686520636c617373206f6620"
            "2739393a204966204920636f756c64206f6666657220796f75206f6e6c79206f6e65207469"
            "7020666f7220746865206675747572652c2073756e73637265656e20776f756c642062652069"
            "742e"
        )
        expected_ciphertext = bytes.fromhex(
            "d31a8d34648e60db7b86afbc53ef7ec2a4aded51296e08fea9e2b5a736ee62d6"
            "3dbea45e8ca9671282fafb69da92728b1a71de0a9e060b2905d6a5b67ecd3b36"
            "92ddbd7f2d778b8c9803aee328091b58fab324e4fad675945585808b4831d7bc"
            "3ff4def08e4b7a9de576d26586cec64b6116"
        )
        ciphertext, tag = aead_encrypt(key, nonce, plaintext, aad)
        self.assertEqual(ciphertext, expected_ciphertext)
        self.assertEqual(tag.hex(), "1ae10b594f09e26a7e902ecbd0600691")
        self.assertEqual(aead_decrypt(key, nonce, ciphertext, tag, aad), plaintext)

    def test_encrypted_wallet_round_trip_and_wrong_password(self):
        wallet = StealthWallet.generate("encrypted-test")
        envelope = wallet.export_encrypted("correct horse battery staple")
        encoded = json.dumps(envelope)
        self.assertNotIn(f"{wallet.keypair.spend_priv:064x}", encoded)
        self.assertNotIn(f"{wallet.keypair.view_priv:064x}", encoded)
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "wallet.json"
            path.write_text(encoded, encoding="utf-8")
            restored = StealthWallet.load_from_json(str(path), "correct horse battery staple")
            self.assertEqual(restored.address, wallet.address)
            with self.assertRaisesRegex(ValueError, "incorrect|modified"):
                StealthWallet.load_from_json(str(path), "wrong password here")

    def test_incremental_sync_cache_is_encrypted_and_restored(self):
        password = "incremental cache test password"
        wallet = StealthWallet.generate("cache-test")
        wallet.utxos = [UTXO(
            txid="ab" * 32, vout=1, amount_satk=123456,
            R_ephem_hex="02" + "11" * 32,
            P_onetime_hex="03" + "22" * 32,
            block_height=9,
        )]
        with tempfile.TemporaryDirectory() as root:
            source = Path(root) / "wallet.json"
            wallet._source_path = source
            wallet._unlock_password = password
            state = {
                "next_height": 10, "anchor_hash": "cd" * 32,
                "network": {**DEVNET_IDENTITY, "name": "devnet"},
            }
            btck_cli._save_sync_cache(wallet, state)
            cache_path = Path(str(source) + ".sync.json")
            encoded = cache_path.read_text(encoding="utf-8")
            self.assertNotIn("ab" * 32, encoded)
            self.assertEqual(cache_path.stat().st_mode & 0o777, 0o600)
            envelope = json.loads(encoded)
            self.assertEqual(envelope["public"]["format"], "btck-wallet-sync-cache-v2")

            restored = StealthWallet(wallet.keypair, "cache-test")
            restored._source_path = source
            restored._unlock_password = password
            loaded = btck_cli._load_sync_cache(restored)
            self.assertEqual(loaded["next_height"], 10)
            self.assertEqual(restored.utxos[0].amount_satk, 123456)

            wallet.utxos[0].spent = True
            btck_cli.persist_current_wallet_state(wallet)
            reloaded = StealthWallet(wallet.keypair, "cache-test")
            reloaded._source_path = source
            reloaded._unlock_password = password
            btck_cli._load_sync_cache(reloaded)
            self.assertTrue(reloaded.utxos[0].spent)

    def test_parallel_wallet_scan_preserves_ordered_spends(self):
        wallet = StealthWallet.generate("parallel-scan")
        foreign = StealthWallet.generate("foreign")
        owned_one = StealthKeyPair.generate_one_time_stealth_output(wallet.address)
        owned_two = StealthKeyPair.generate_one_time_stealth_output(wallet.address)
        foreign_output = StealthKeyPair.generate_one_time_stealth_output(foreign.address)

        def output(txid, amount, pair, height):
            return {
                "txid": txid, "vout": 0, "amount_satk": amount,
                "R_ephem_hex": pair[0].hex(), "P_onetime_hex": pair[1].hex(),
                "height": height, "is_coinbase": True,
                "timelock_until_height": 0, "kind": "miner",
            }

        page = {
            "format": "btck-wallet-sync-v1",
            "network": btck_cli.APPROVED_NETWORKS["devnet"],
            "from_height": 0, "next_height": 2,
            "anchor_hash": "02" * 32, "tip_height": 1,
            "done": True,
            "blocks": [
                {
                    "height": 0, "hash": "01" * 32, "spent": [],
                    "created": [
                        output("11" * 32, 100, owned_one, 0),
                        output("22" * 32, 999, foreign_output, 0),
                    ],
                },
                {
                    "height": 1, "hash": "02" * 32,
                    "spent": [{"txid": "11" * 32, "vout": 0}],
                    "created": [output("33" * 32, 200, owned_two, 1)],
                },
            ],
        }
        args = SimpleNamespace(sync_workers=2)
        with patch.object(btck_cli, "_fetch_sync_page", return_value=page):
            snapshot, network = btck_cli.scan_wallet(args, wallet)
        self.assertEqual(snapshot["selected_height"], 1)
        self.assertEqual(network["name"], "devnet")
        self.assertEqual([(item.txid, item.amount_satk) for item in wallet.utxos], [("33" * 32, 200)])

    def test_legacy_v1_sync_cache_remains_readable(self):
        password = "legacy cache compatibility password"
        wallet = StealthWallet.generate("legacy-cache")
        with tempfile.TemporaryDirectory() as root:
            source = Path(root) / "wallet.json"
            wallet._source_path = source
            wallet._unlock_password = password
            legacy_utxo = UTXO(
                txid="ef" * 32, vout=2, amount_satk=987654,
                R_ephem_hex="02" + "33" * 32,
                P_onetime_hex="03" + "44" * 32,
                block_height=12,
            )
            private = {
                "next_height": 13,
                "anchor_hash": "aa" * 32,
                "network": {**DEVNET_IDENTITY, "name": "devnet"},
                "utxos": [vars(legacy_utxo)],
            }
            public = {
                "format": "btck-wallet-sync-cache-v1",
                "wallet_address": wallet.address,
            }
            Path(str(source) + ".sync.json").write_text(
                json.dumps(encrypt_wallet_payload(private, public, password)),
                encoding="utf-8",
            )
            state = btck_cli._load_sync_cache(wallet)
            self.assertEqual(state["next_height"], 13)
            self.assertEqual(wallet.utxos[0].amount_satk, 987654)

    def test_cli_mnemonic_create_restore_verify_and_eco_output(self):
        mnemonic = "abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon about"
        password = "mnemonic wallet test password"
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            password_path = root / "password.txt"
            mnemonic_path = root / "mnemonic.txt"
            wallet_path = root / "wallet.json"
            restored_path = root / "restored.json"
            eco_path = root / "eco.json"
            password_path.write_text(password + "\n", encoding="utf-8")
            mnemonic_path.write_text(mnemonic + "\n", encoding="utf-8")
            password_path.chmod(0o600)
            mnemonic_path.chmod(0o600)
            common = {"password_file": str(password_path), "allow_legacy_plaintext": False}

            create_args = SimpleNamespace(
                **common, name="recoverable", words=12, output=str(wallet_path),
                skip_immediate_recovery_check=True,
            )
            with patch.object(btck_cli, "generate_mnemonic", return_value=mnemonic), patch.object(
                btck_cli, "_print_recovery_sheet"
            ):
                btck_cli.cmd_new(create_args)
            envelope = json.loads(wallet_path.read_text(encoding="utf-8"))
            self.assertEqual(envelope["recovery"]["scheme"], btck_cli.RECOVERY_SCHEME)
            self.assertNotIn("abandon", wallet_path.read_text(encoding="utf-8"))

            verify_args = SimpleNamespace(
                **common, wallet=str(wallet_path), mnemonic_file=str(mnemonic_path),
            )
            btck_cli.cmd_verify_recovery(verify_args)

            restore_args = SimpleNamespace(
                **common, name="restored", output=str(restored_path),
                account_index=0, words=12, mnemonic_file=str(mnemonic_path),
            )
            btck_cli.cmd_restore(restore_args)
            original = StealthWallet.load_from_json(str(wallet_path), password=password)
            restored = StealthWallet.load_from_json(str(restored_path), password=password)
            self.assertEqual(original.address, restored.address)

            eco_args = SimpleNamespace(**common, wallet=str(wallet_path), out=str(eco_path))
            btck_cli.cmd_eco_output(eco_args)
            eco = json.loads(eco_path.read_text(encoding="utf-8"))
            r_ephem, p_onetime = (bytes.fromhex(item) for item in eco["eco_fund_output"])
            self.assertTrue(original.keypair.is_my_stealth_output(r_ephem, p_onetime))

    def test_mainnet_wallet_suite_uses_three_independent_recovery_phrases(self):
        password = "three wallet suite test password"
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            password_path = root / "password.txt"
            output_dir = root / "suite"
            password_path.write_text(password + "\n", encoding="utf-8")
            password_path.chmod(0o600)
            captured = []
            args = SimpleNamespace(
                password_file=str(password_path), allow_legacy_plaintext=False,
                output_dir=str(output_dir), words=24,
                skip_immediate_recovery_check=True, resume=False,
            )
            with patch.object(
                btck_cli, "_print_recovery_sheet",
                side_effect=lambda name, address, mnemonic: captured.append((name, address, mnemonic)),
            ):
                btck_cli.cmd_init_mainnet_wallets(args)
            self.assertEqual(len(captured), 3)
            self.assertEqual(len({item[2] for item in captured}), 3)
            self.assertTrue(all(len(item[2].split()) == 24 for item in captured))
            public = json.loads((output_dir / "mainnet_wallet_public.json").read_text(encoding="utf-8"))
            eco_wallet = StealthWallet.load_from_json(
                str(output_dir / "eco_fund_wallet.json"), password=password,
            )
            r_ephem, p_onetime = (bytes.fromhex(item) for item in public["eco_fund_output"])
            self.assertTrue(eco_wallet.keypair.is_my_stealth_output(r_ephem, p_onetime))
            self.assertEqual(oct((output_dir / "personal_wallet.json").stat().st_mode & 0o777), "0o600")

    def test_mainnet_wallet_suite_resumes_after_first_wallet_confirmation_abort(self):
        mnemonic = "abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon about"
        password = "resume wallet suite password"
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            output_dir = root / "suite"
            output_dir.mkdir(mode=0o700)
            password_path = root / "password.txt"
            password_path.write_text(password + "\n", encoding="utf-8")
            password_path.chmod(0o600)
            personal = btck_cli._wallet_from_mnemonic(
                mnemonic, "BTC-K Mainnet Personal / Mining",
            )
            btck_cli.write_private_json(
                output_dir / "personal_wallet.json",
                btck_cli._encrypted_recoverable_wallet(
                    personal, password, word_count=12,
                ),
            )
            args = SimpleNamespace(
                password_file=str(password_path), allow_legacy_plaintext=False,
                output_dir=str(output_dir), words=24,
                skip_immediate_recovery_check=True, resume=True,
            )
            with patch.object(btck_cli, "_confirm_recovery") as confirm, patch.object(
                btck_cli, "_print_recovery_sheet"
            ):
                btck_cli.cmd_init_mainnet_wallets(args)
            confirm.assert_called_once()
            self.assertTrue((output_dir / "pool_treasury_wallet.json").exists())
            self.assertTrue((output_dir / "eco_fund_wallet.json").exists())
            public = json.loads((output_dir / "mainnet_wallet_public.json").read_text(encoding="utf-8"))
            self.assertEqual(public["personal_address"], personal.address)

    def test_pool_treasury_requires_encryption_password_and_matching_address(self):
        wallet = StealthWallet.generate("pool")
        password = "pool encrypted wallet password"
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            wallet_path = root / "pool.json"
            password_path = root / "password.txt"
            plaintext_path = root / "legacy.json"
            wallet_path.write_text(json.dumps(wallet.export_encrypted(password)), encoding="utf-8")
            password_path.write_text(password + "\n", encoding="utf-8")
            plaintext_path.write_text(
                json.dumps(wallet.export_to_dict(include_private=True)), encoding="utf-8",
            )
            wallet_path.chmod(0o600)
            password_path.chmod(0o600)
            plaintext_path.chmod(0o600)

            loaded = load_pool_treasury_wallet(
                str(wallet_path), password_path=str(password_path),
                require_encryption=True, expected_address=wallet.address,
            )
            self.assertEqual(loaded.address, wallet.address)
            with self.assertRaisesRegex(ValueError, "does not match"):
                load_pool_treasury_wallet(
                    str(wallet_path), password_path=str(password_path),
                    require_encryption=True, expected_address=StealthWallet.generate().address,
                )
            with self.assertRaisesRegex(ValueError, "plaintext"):
                load_pool_treasury_wallet(
                    str(plaintext_path), require_encryption=True,
                    expected_address=wallet.address,
                )
            wallet_path.chmod(0o644)
            with self.assertRaisesRegex(ValueError, "permissions"):
                load_pool_treasury_wallet(
                    str(wallet_path), password_path=str(password_path),
                    require_encryption=True, expected_address=wallet.address,
                )


class TestOfflineWalletFlow(unittest.TestCase):
    def test_wallet_sync_reports_page_progress_to_graphical_clients(self):
        wallet = MagicMock()
        wallet.utxos = []
        network = {**DEVNET_IDENTITY, "name": "devnet"}
        pages = [
            {
                "format": "btck-wallet-sync-v1", "network": network,
                "from_height": 0, "next_height": 1, "anchor_hash": "01" * 32,
                "tip_height": 1, "done": False,
                "blocks": [{"height": 0, "hash": "01" * 32, "spent": [], "created": []}],
            },
            {
                "format": "btck-wallet-sync-v1", "network": network,
                "from_height": 1, "next_height": 2, "anchor_hash": "02" * 32,
                "tip_height": 1, "done": True,
                "blocks": [{"height": 1, "hash": "02" * 32, "spent": [], "created": []}],
            },
        ]
        progress = []
        with patch.object(
            btck_cli, "_load_sync_cache",
            return_value={"next_height": 0, "anchor_hash": "", "network": None},
        ), patch.object(btck_cli, "_save_sync_cache"), patch.object(
            btck_cli, "_fetch_sync_page", side_effect=pages,
        ):
            snapshot, actual_network = btck_cli.scan_wallet(
                SimpleNamespace(), wallet, progress_callback=progress.append,
            )
        self.assertEqual(snapshot["selected_height"], 1)
        self.assertEqual(actual_network, btck_cli.validate_network_identity(network))
        self.assertEqual([item["percent"] for item in progress], [50.0, 100.0])
        self.assertEqual([item["completed"] for item in progress], [1, 2])
        self.assertTrue(all("tracked_satk" in item for item in progress))
        self.assertTrue(all("spendable_satk" in item for item in progress))

    def test_cli_transfer_preserves_spent_state_when_broadcast_status_is_unknown(self):
        wallet = MagicMock()
        wallet.create_and_sign_transfer.return_value = MagicMock()
        args = SimpleNamespace(
            offline=False, wallet="wallet.json", to="recipient", amount="1",
            fee="0.0001",
        )
        snapshot = {"selected_height": 100}
        network = {**DEVNET_IDENTITY, "name": "devnet"}
        with patch.object(btck_cli, "load_wallet", return_value=wallet), patch.object(
            btck_cli, "scan_wallet", return_value=(snapshot, network),
        ), patch.object(btck_cli, "make_signed_package", return_value={}), patch.object(
            btck_cli, "broadcast_signed_package",
            side_effect=btck_cli.BroadcastStatusUnknown("status is unknown"),
        ), patch.object(btck_cli, "persist_current_wallet_state") as persist:
            with self.assertRaises(btck_cli.BroadcastStatusUnknown):
                btck_cli.cmd_transfer(args)
        persist.assert_called_once_with(wallet)

    def test_broadcast_distinguishes_explicit_rejection_from_unknown_status(self):
        args = SimpleNamespace(broadcast_url="https://wallet.example/broadcast", timeout=1.0)
        package = {"format": "btck-signed-transfer-v1"}

        rejection = HTTPError(
            args.broadcast_url, 400, "Bad Request", {},
            BytesIO(b'{"message":"invalid transaction"}'),
        )
        with patch.object(btck_cli, "urlopen", side_effect=rejection):
            with self.assertRaisesRegex(btck_cli.CLIError, "rejected.*invalid transaction"):
                btck_cli.broadcast_signed_package(args, package)

        with patch.object(btck_cli, "urlopen", side_effect=URLError("response lost")):
            with self.assertRaisesRegex(btck_cli.BroadcastStatusUnknown, "status is unknown"):
                btck_cli.broadcast_signed_package(args, package)

    def test_broadcast_refuses_unbound_raw_transaction_without_rpc(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "raw.json"
            path.write_text(json.dumps({"transaction": "unbound"}), encoding="utf-8")
            args = SimpleNamespace(transaction=str(path))
            with patch.object(
                btck_cli, "rpc_call", side_effect=AssertionError("unbound payload used RPC"),
            ):
                with self.assertRaisesRegex(btck_cli.CLIError, "Unbound raw transaction"):
                    btck_cli.cmd_broadcast(args)

    def test_wallet_scans_refuse_wrong_consensus_before_requesting_utxos(self):
        wallet = MagicMock()
        bad_page = {
            "format": "btck-wallet-sync-v1",
            "network": {**DEVNET_IDENTITY, "name": "devnet", "consensus_fingerprint": "00" * 32},
            "from_height": 0, "next_height": 1, "anchor_hash": "11" * 32,
            "tip_height": 0, "done": True, "blocks": [],
        }

        with patch.object(btck_cli, "_fetch_sync_page", return_value=bad_page):
            with self.assertRaisesRegex(btck_cli.CLIError, "unknown|approved"):
                btck_cli.scan_wallet(SimpleNamespace(), wallet)
        wallet.scan_chain_outputs.assert_not_called()

    def test_prepare_sign_broadcast_artifact_without_rpc_during_sign(self):
        sender = StealthWallet.generate("sender")
        recipient = StealthWallet.generate("recipient")
        R, P = StealthKeyPair.generate_one_time_stealth_output(sender.address)
        password = "offline signing test password"
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            wallet_path = root / "wallet.json"
            watch_path = root / "watch.json"
            password_path = root / "password.txt"
            unsigned_path = root / "unsigned.json"
            signed_path = root / "signed.json"
            wallet_path.write_text(json.dumps(sender.export_encrypted(password)), encoding="utf-8")
            password_path.write_text(password + "\n", encoding="utf-8")
            password_path.chmod(0o600)
            common = {"password_file": str(password_path), "allow_legacy_plaintext": False}
            btck_cli.cmd_export_watch(SimpleNamespace(**common, wallet=str(wallet_path), out=str(watch_path)))

            sync_blocks = [
                {"height": height, "hash": f"{height + 1:064x}", "spent": [], "created": []}
                for height in range(101)
            ]
            sync_blocks[50]["created"] = [{
                "txid": "11" * 32, "vout": 0, "amount_satk": 500_000,
                "R_ephem_hex": R.hex(), "P_onetime_hex": P.hex(), "height": 50,
            }]
            sync_page = {
                "format": "btck-wallet-sync-v1", "network": {**DEVNET_IDENTITY, "name": "devnet"},
                "from_height": 0, "next_height": 101, "anchor_hash": f"{101:064x}",
                "tip_height": 100, "done": True, "blocks": sync_blocks,
            }

            prepare_args = SimpleNamespace(
                **common, watch_wallet=str(watch_path), to=recipient.address, amount="0.003",
                fee="0.0001", out=str(unsigned_path), rpc_token=None,
            )
            with patch.object(btck_cli, "_fetch_sync_page", return_value=sync_page):
                btck_cli.cmd_prepare(prepare_args)

            package_id = json.loads(unsigned_path.read_text(encoding="utf-8"))["package_id"]
            sign_args = SimpleNamespace(
                **common, wallet=str(wallet_path), package=str(unsigned_path), out=str(signed_path),
                approve_package_id=package_id,
            )
            with patch.object(btck_cli, "rpc_call", side_effect=AssertionError("offline signer used RPC")):
                btck_cli.cmd_sign(sign_args)
            signed = json.loads(signed_path.read_text(encoding="utf-8"))
            transaction = transaction_from_dict(signed["transaction"])
            self.assertTrue(StealthWallet.verify_transaction(transaction))
            self.assertEqual(signed["network"]["name"], "devnet")

    def test_prepare_excludes_immature_and_timelocked_outputs(self):
        sender = StealthWallet.generate("sender")
        recipient = StealthWallet.generate("recipient")
        outputs = []
        for index, metadata in enumerate((
            {"is_coinbase": True, "height": 9_900},
            {"timelock_until_height": 11_000, "height": 9_000},
            {"height": 9_000},
        )):
            R, P = StealthKeyPair.generate_one_time_stealth_output(sender.address)
            outputs.append({
                "txid": f"{index + 1:064x}", "vout": 0, "amount_satk": 500_000,
                "R_ephem_hex": R.hex(), "P_onetime_hex": P.hex(), **metadata,
            })
        sender.scan_chain_outputs(outputs)
        self.assertEqual(sender.get_balance_satk(), 1_500_000)
        self.assertEqual(sender.get_spendable_balance_satk(10_000), 500_000)

    def test_offline_signer_rejects_unknown_network_even_with_matching_digest(self):
        package = {
            "format": "btck-unsigned-transfer-v1",
            "created_at": 1,
            "network": {"name": "devnet", "chain_id": "attacker", "genesis_hash": "11" * 32},
            "selected_height": 1,
            "sender_address": "unused",
            "recipient_address": "unused",
            "amount_satk": 1,
            "fee_satk": 0,
            "inputs": [],
        }
        package["package_id"] = btck_cli._package_id(package)
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "unsigned.json"
            path.write_text(json.dumps(package), encoding="utf-8")
            args = SimpleNamespace(
                package=str(path), approve_package_id=package["package_id"],
                wallet="unused", out="unused", password_file=None,
                allow_legacy_plaintext=False,
            )
            with self.assertRaisesRegex(btck_cli.CLIError, "unknown|approved"):
                btck_cli.cmd_sign(args)


if __name__ == "__main__":
    unittest.main()
