"""
Official Kaspa kHeavyHash Specification Test Vectors
---------------------------------------------------
Verifies bit-for-bit equivalence against:
- kaspanet/kaspad domain/consensus/utils/pow/heavyhash_test.go
- kaspanet/rusty-kaspa crypto/hashes/src/pow_hashers.rs
"""

import unittest
import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from consensus.kheavyhash import (
    generate_matrix,
    heavy_hash,
    cshake256,
    kheavyhash,
    hash_to_target_int,
    difficulty_to_target,
    compute_rank
)
from kaspa_test_matrix import KASPA_TEST_MATRIX

class TestKaspaKHeavyHashVectors(unittest.TestCase):

    def test_seed42_matrix_generation(self):
        seed = bytes([42] * 32)
        mat = generate_matrix(seed)
        self.assertEqual(len(mat), 64)
        self.assertEqual(len(mat[0]), 64)
        self.assertEqual(compute_rank(mat), 64)

        # First row expected from kaspad TestGenerateMatrix
        expected_row0 = [
            4, 5, 4, 5, 4, 5, 4, 5, 4, 5, 4, 5, 4, 5, 4, 5,
            15, 3, 15, 3, 15, 3, 15, 3, 15, 3, 15, 3, 15, 3, 15, 3,
            2, 10, 2, 10, 2, 10, 2, 10, 2, 10, 2, 10, 2, 10, 2, 10,
            14, 1, 2, 2, 14, 10, 4, 12, 4, 12, 10, 10, 10, 10, 10, 10
        ]
        self.assertEqual(mat[0], expected_row0)

    def test_cshake256_proof_of_work_hash(self):
        input_bytes = bytes([0xC1, 0xEC, 0xFD, 0xFC])
        pow_hash = cshake256(input_bytes, N=b'', S=b'ProofOfWorkHash', length=32)
        expected_hex = "522ed4da1cc08f5cd542563ff5f19bbd499fe5b4ca699fa66dac8088a9c36129"
        self.assertEqual(pow_hash.hex(), expected_hex)

    def test_target_math(self):
        diff1 = 1.0
        target1 = difficulty_to_target(diff1)
        max_target = (1 << 256) - 1
        self.assertEqual(target1, max_target)

        diff2 = 2.0
        target2 = difficulty_to_target(diff2)
        self.assertEqual(target2, max_target // 2)

    def test_official_kaspa_heavyhash_vector(self):
        # Official testMatrix from kaspanet/kaspad heavyhash_test.go
        input_bytes = bytes([0xC1, 0xEC, 0xFD, 0xFC])
        pow_hash = cshake256(input_bytes, N=b'', S=b'ProofOfWorkHash', length=32)
        final_hash = heavy_hash(KASPA_TEST_MATRIX, pow_hash)
        expected_hash = "87689f379943eaf9b7475ca95325687772bfcc68fc7899caeb4409ec4590c325"
        self.assertEqual(final_hash.hex(), expected_hash)

if __name__ == "__main__":
    unittest.main()
