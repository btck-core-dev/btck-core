"""Platform paths and actual Windows permissions for wallet persistence."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "tools")]
import btck_cli
from privacy import wallet_registry
from privacy.private_files import restrict_private_path


class WalletPlatformTests(unittest.TestCase):
    def test_windows_uses_per_user_local_appdata(self):
        with patch.object(wallet_registry.sys, "platform", "win32"), \
             patch.dict(os.environ, {"LOCALAPPDATA": str(Path.home() / "appdata-test")}):
            self.assertEqual(wallet_registry.default_registry_path(),
                             Path.home() / "appdata-test" / "Bitcoin K Wallet" / "wallets.json")

    def test_mac_preserves_existing_registry_location(self):
        with patch.object(wallet_registry.sys, "platform", "darwin"):
            self.assertEqual(wallet_registry.default_registry_path(),
                             Path.home() / "Library" / "Application Support" /
                             "Bitcoin K Wallet" / "wallets.json")

    def test_permission_failure_never_writes_wallet_payload(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "wallet.json"
            with patch.object(btck_cli, "restrict_private_path", side_effect=OSError("denied")):
                with self.assertRaises(OSError):
                    btck_cli.write_private_json(path, {"sensitive": "never-write"})
            self.assertEqual(path.read_bytes(), b"")

    @unittest.skipUnless(os.name == "nt", "requires actual NTFS permissions")
    def test_windows_registry_and_wallet_have_protected_account_only_acl(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            wallet = root / "wallet.json"
            btck_cli.write_private_json(wallet, {"format": "disposable"})
            registry = wallet_registry.WalletRegistry(root / "app" / "wallets.json")
            registry.add(wallet, "Disposable")
            for path in (wallet, registry.path, registry.path.parent):
                env = dict(os.environ, BTCK_ACL_TEST_PATH=str(path))
                command = (
                    "$a=Get-Acl -LiteralPath $env:BTCK_ACL_TEST_PATH; "
                    "$u=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value; "
                    "$s=@($a.Access | ForEach-Object { "
                    "$_.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value }); "
                    "@{protected=$a.AreAccessRulesProtected; current=$u; sids=$s} | ConvertTo-Json -Compress"
                )
                result = subprocess.check_output(
                    ["powershell", "-NoProfile", "-Command", command], env=env, text=True)
                acl = json.loads(result)
                self.assertTrue(acl["protected"])
                self.assertEqual(set(acl["sids"]), {acl["current"], "S-1-5-18"})
                # Reapplying restrictions must preserve the ability to write.
                restrict_private_path(path, directory=path.is_dir())
            self.assertEqual(json.loads(wallet.read_text())["format"], "disposable")


if __name__ == "__main__":
    unittest.main()
