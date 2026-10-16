"""Tests for the non-secret multi-wallet application registry."""

import json
import tempfile
import unittest
from pathlib import Path

from privacy.wallet_registry import REGISTRY_FORMAT, WalletRegistry, WalletRegistryError


class TestWalletRegistry(unittest.TestCase):
    def test_add_rename_remove_and_reload_independent_wallets(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            registry_path = root / "app" / "wallets.json"
            first_path = root / "first.json"
            second_path = root / "second.json"
            first_path.write_text("{}", encoding="utf-8")
            second_path.write_text("{}", encoding="utf-8")

            registry = WalletRegistry(registry_path)
            first = registry.add(first_path, "Personal")
            second = registry.add(second_path, "Mining")
            self.assertNotEqual(first["id"], second["id"])
            self.assertEqual(len(registry.records), 2)
            self.assertEqual(registry_path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(registry_path.parent.stat().st_mode & 0o777, 0o700)

            registry.rename(second["id"], "Mining Vault")
            reloaded = WalletRegistry(registry_path)
            self.assertEqual(reloaded.get(second["id"])["label"], "Mining Vault")
            self.assertEqual(reloaded.get(first["id"])["account_model"], "independent")

            reloaded.remove(first["id"])
            self.assertIsNone(reloaded.get(first["id"]))
            self.assertTrue(first_path.exists())

    def test_duplicate_path_updates_label_without_duplicating(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            registry = WalletRegistry(root / "wallets.json")
            wallet_path = root / "wallet.json"
            first = registry.add(wallet_path, "First")
            second = registry.add(wallet_path, "Updated")
            self.assertEqual(first["id"], second["id"])
            self.assertEqual(len(registry.records), 1)
            self.assertEqual(registry.records[0]["label"], "Updated")

    def test_registry_rejects_tampered_identity_and_never_contains_secrets(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "wallets.json"
            path.write_text(json.dumps({
                "format": REGISTRY_FORMAT,
                "wallets": [{
                    "id": "attacker-selected-id",
                    "label": "Wallet",
                    "path": str(Path(root) / "wallet.json"),
                    "network": "mainnet",
                    "account_model": "independent",
                    "password": "must-not-be-accepted",
                }],
            }), encoding="utf-8")
            with self.assertRaisesRegex(WalletRegistryError, "identity"):
                WalletRegistry(path)

    def test_invalid_labels_are_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            registry = WalletRegistry(Path(root) / "wallets.json")
            with self.assertRaisesRegex(WalletRegistryError, "empty"):
                registry.add(Path(root) / "wallet.json", "   ")
            with self.assertRaisesRegex(WalletRegistryError, "64"):
                registry.add(Path(root) / "wallet.json", "x" * 65)


if __name__ == "__main__":
    unittest.main()
