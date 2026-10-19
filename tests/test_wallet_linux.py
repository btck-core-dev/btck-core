"""Linux data locations, desktop launcher escaping and private persistence."""
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "tools")]
import btck_cli
from privacy import wallet_registry
from wallet_desktop import desktop_exec, install_desktop_entry


class LinuxWalletTests(unittest.TestCase):
    def test_xdg_location_and_relative_fallback(self):
        with patch.object(sys, "platform", "linux"), tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            with patch.dict(os.environ, {"XDG_DATA_HOME": str(base)}):
                self.assertEqual(wallet_registry.default_registry_path(),
                                 base / "bitcoin-k-wallet" / "wallets.json")
            for value in ("", "relative/path"):
                with patch.dict(os.environ, {"XDG_DATA_HOME": value}):
                    self.assertEqual(wallet_registry.default_registry_path(),
                                     Path.home() / ".local/share/bitcoin-k-wallet/wallets.json")

    @unittest.skipUnless(sys.platform.startswith("linux"), "actual POSIX Linux filesystem")
    def test_private_registry_wallet_and_desktop_install(self):
        with tempfile.TemporaryDirectory(prefix="btck Linux 钱包 ") as directory:
            base = Path(directory)
            wallet = base / "wallet.json"
            btck_cli.write_private_json(wallet, {"disposable": True})
            registry = wallet_registry.WalletRegistry(base / "private" / "wallets.json")
            registry.add(wallet, "Disposable")
            for path in (wallet, registry.path):
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(registry.path.parent.stat().st_mode & 0o777, 0o700)
            with patch.dict(os.environ, {"XDG_DATA_HOME": str(base / "share")}), \
                 patch.object(sys, "frozen", True, create=True):
                executable = base / 'spaces "quote" $cash `tick` 20% /BTC-K Wallet'
                entry = install_desktop_entry(executable)
                self.assertIn("Exec=" + desktop_exec(executable), entry.read_text())
                self.assertIn("20%%", entry.read_text())
                target = base / "do-not-overwrite"
                target.write_text("keep")
                entry.unlink()
                entry.symlink_to(target)
                install_desktop_entry(executable)
                self.assertEqual(target.read_text(), "keep")
                self.assertFalse(entry.is_symlink())

    def test_control_characters_rejected(self):
        with self.assertRaises(ValueError):
            desktop_exec(Path("/tmp/invalid\npath"))


if __name__ == "__main__":
    unittest.main()
