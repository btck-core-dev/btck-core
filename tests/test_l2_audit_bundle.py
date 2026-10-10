"""Security boundaries for the reproducible external-audit bundle."""

import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../scripts")))

import build_l2_audit_bundle as bundle


class TestL2AuditBundle(unittest.TestCase):
    def test_required_scope_and_secret_exclusions_do_not_overlap(self):
        self.assertIn("src/gateway/l2_chain_bridge.py", bundle.EXACT_FILES)
        self.assertIn("src/tokenomics/provable_burn.py", bundle.EXACT_FILES)
        for relative in bundle.EXACT_FILES:
            path = Path(relative)
            self.assertFalse(bundle.FORBIDDEN_PARTS.intersection(path.parts))
            self.assertNotIn("wallet.json", path.name)
            self.assertNotIn("privkey", path.name)


if __name__ == "__main__":
    unittest.main()
