"""
Test Suite: BTC-K L1 Consensus Safeguards, Satoshi-Style Alert System,
Height-Activated Soft-Fork Registry, and Rolling Checkpoint Guard.
"""

import unittest
import time
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from consensus.safeguards import (
    AlertAction,
    NetworkAlertPayload,
    SignedNetworkAlert,
    AlertManager,
    SoftForkManager,
    RollingCheckpointGuard,
    ConsensusGuardian,
    NETWORK_DEFENSE_PUBLIC_KEY_HEX
)
from privacy.wallet_engine import schnorr_sign, sha256

class TestConsensusSafeguards(unittest.TestCase):

    def setUp(self):
        # Master testing keypair
        self.priv_scalar = 0x403dde990f580ac8bbc8619490614cd5efc05a764792a86bd64e71514407f517
        self.pub_hex = "02a1a9de79ff6fcc1c8399d74aa348532f64fbe0f75d0f354d9d57aae60a6a19d8"
        self.alert_mgr = AlertManager(pubkey_hex=self.pub_hex)
        self.softfork_mgr = SoftForkManager()
        self.checkpoint_guard = RollingCheckpointGuard()

    def _sign_payload(self, payload: NetworkAlertPayload) -> SignedNetworkAlert:
        digest = payload.compute_digest()
        sig = schnorr_sign(self.priv_scalar, digest)
        return SignedNetworkAlert(payload=payload, signature_hex=sig.hex())

    def test_01_alert_signature_verification_and_tampering(self):
        payload = NetworkAlertPayload(
            alert_id=101,
            version=1,
            expiration_timestamp=int(time.time()) + 3600,
            priority=2,
            actions=[AlertAction.NOTICE],
            message="Advisory: Scheduled maintenance."
        )
        signed_alert = self._sign_payload(payload)

        # 1. Verification succeeds
        valid, msg = self.alert_mgr.process_incoming_alert(signed_alert)
        self.assertTrue(valid)
        self.assertEqual(self.alert_mgr.get_latest_broadcast_notice(), "Advisory: Scheduled maintenance.")

        # 2. Tampered message fails
        signed_alert.payload.message = "Tampered message!"
        valid, msg = self.alert_mgr.process_incoming_alert(signed_alert)
        self.assertFalse(valid)
        self.assertIn("failed", msg.lower())

    def test_02_safe_mode_trigger_and_cancellation(self):
        guardian = ConsensusGuardian()
        guardian.alerts = AlertManager(pubkey_hex=self.pub_hex)

        # Initially Safe Mode is inactive
        self.assertFalse(guardian.alerts.is_safe_mode())
        guardian.check_safe_mode_or_raise()

        # Trigger Emergency Safe Mode
        safe_payload = NetworkAlertPayload(
            alert_id=201,
            version=1,
            expiration_timestamp=int(time.time()) + 3600,
            priority=3,
            actions=[AlertAction.ENABLE_SAFE_MODE],
            message="CRITICAL: Node entering read-only safe mode for network defense."
        )
        signed_safe = self._sign_payload(safe_payload)
        ok, _ = guardian.alerts.process_incoming_alert(signed_safe)
        self.assertTrue(ok)
        self.assertTrue(guardian.alerts.is_safe_mode())

        # Operation must be blocked
        with self.assertRaises(PermissionError):
            guardian.check_safe_mode_or_raise()

        # Clear Safe Mode with a follow-up directive
        clear_payload = NetworkAlertPayload(
            alert_id=202,
            version=1,
            expiration_timestamp=int(time.time()) + 3600,
            priority=3,
            actions=[AlertAction.CLEAR_SAFE_MODE],
            message="Safe mode lifted. Network regular operations resumed.",
            cancel_alert_id=201
        )
        signed_clear = self._sign_payload(clear_payload)
        ok, _ = guardian.alerts.process_incoming_alert(signed_clear)
        self.assertTrue(ok)
        self.assertFalse(guardian.alerts.is_safe_mode())
        guardian.check_safe_mode_or_raise()

    def test_03_softfork_manager_height_activation(self):
        self.softfork_mgr.register_rule(
            rule_id="BIP_SF_001_MATURITY_PATCH",
            activation_height=50_000,
            description="Extend coinbase maturity rule",
            parameters={"maturity_blocks": 14_400}
        )

        # Prior to activation height
        self.assertFalse(self.softfork_mgr.is_rule_active("BIP_SF_001_MATURITY_PATCH", 49_999))
        self.assertEqual(self.softfork_mgr.get_parameter("BIP_SF_001_MATURITY_PATCH", "maturity_blocks", 7_200, 49_999), 7_200)

        # At activation height
        self.assertTrue(self.softfork_mgr.is_rule_active("BIP_SF_001_MATURITY_PATCH", 50_000))
        self.assertEqual(self.softfork_mgr.get_parameter("BIP_SF_001_MATURITY_PATCH", "maturity_blocks", 7_200, 50_000), 14_400)

    def test_04_rolling_checkpoint_guard(self):
        guard = RollingCheckpointGuard()

        # Genesis checkpoint matches
        self.assertTrue(guard.is_block_valid_against_checkpoints(0, "genesis_block_0"))

        # Add a rolling checkpoint at height 10,000
        guard.add_checkpoint(10_000, "00000000abc123def456")

        # Correct hash passes
        self.assertTrue(guard.is_block_valid_against_checkpoints(10_000, "00000000abc123def456"))

        # Invalid reorg hash fails
        self.assertFalse(guard.is_block_valid_against_checkpoints(10_000, "bad_reorg_block_hash"))

        # Non-checkpoint heights pass automatically
        self.assertTrue(guard.is_block_valid_against_checkpoints(9_999, "any_valid_hash"))

if __name__ == "__main__":
    unittest.main()
