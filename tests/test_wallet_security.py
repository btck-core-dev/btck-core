"""Encrypted wallet and genuinely offline signing workflow tests."""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "tools")))

import btck_cli
from privacy.stealth_address import StealthKeyPair
from privacy.wallet_crypto import aead_decrypt, aead_encrypt
from privacy.wallet_engine import StealthWallet, transaction_from_dict


class TestWalletEncryption(unittest.TestCase):
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


class TestOfflineWalletFlow(unittest.TestCase):
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

            def fake_rpc(_args, method, _params=None):
                if method == "getUTXOSet":
                    return {"selected_height": 100, "outputs": [{
                        "txid": "11" * 32, "vout": 0, "amount_satk": 500_000,
                        "R_ephem_hex": R.hex(), "P_onetime_hex": P.hex(), "height": 50,
                    }]}
                if method == "getBlockDagInfo":
                    return {"network": "devnet", "chain_id": "btck-devnet-v3", "genesis_hash": "22" * 32}
                raise AssertionError(method)

            prepare_args = SimpleNamespace(
                **common, watch_wallet=str(watch_path), to=recipient.address, amount="0.003",
                fee="0.0001", out=str(unsigned_path), rpc_token=None,
            )
            with patch.object(btck_cli, "rpc_call", side_effect=fake_rpc):
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


if __name__ == "__main__":
    unittest.main()
