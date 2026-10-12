import os
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "deploy" / "install_seed_node.sh"
RUNNER = ROOT / "deploy" / "run_seed_node.sh"
SERVICE = ROOT / "deploy" / "btckd.service"


class SeedDeploymentTests(unittest.TestCase):
    def test_shell_scripts_parse_and_installer_help_is_unprivileged(self):
        subprocess.run(["bash", "-n", INSTALLER, RUNNER], check=True)
        completed = subprocess.run(
            [INSTALLER, "--help"], check=True, capture_output=True, text=True
        )
        self.assertIn("--trusted-public-key", completed.stdout)
        self.assertIn("leaves it stopped", completed.stdout)

    def test_runner_rejects_public_rpc_binding(self):
        environment = {
            **os.environ,
            "BTCK_NETWORK": "mainnet",
            "BTCK_NETWORK_MANIFEST": "/opt/btck/current/params/networks/mainnet.json",
            "BTCK_DATA_ROOT": "/tmp/btck-test",
            "BTCK_RPC_HOST": "0.0.0.0",
            "BTCK_RPC_PORT": "16110",
            "BTCK_P2P_HOST": "0.0.0.0",
            "BTCK_P2P_PORT": "16111",
        }
        completed = subprocess.run(
            [RUNNER], env=environment, capture_output=True, text=True
        )
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("Refusing to expose JSON-RPC", completed.stderr)

    def test_service_is_checkpoint_bound_and_hardened(self):
        service = SERVICE.read_text(encoding="utf-8")
        self.assertIn("ExecStart=/opt/btck/current/deploy/run_seed_node.sh", service)
        self.assertIn("ProtectSystem=strict", service)
        self.assertIn("CapabilityBoundingSet=", service)
        self.assertNotIn("devnet", service)

    def test_installer_is_signature_gated_and_does_not_change_firewall(self):
        installer = INSTALLER.read_text(encoding="utf-8")
        self.assertIn("verify_mainnet_release.py", installer)
        self.assertIn("PUBLIC-MAINNET-CHECKPOINT-BOUND", installer)
        self.assertIn("archive.extractall(staging)", installer)
        self.assertNotIn("ufw ", installer)
        self.assertNotIn("iptables ", installer)
        self.assertNotIn("firewall-cmd ", installer)


if __name__ == "__main__":
    unittest.main()
