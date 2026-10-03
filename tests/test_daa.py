import unittest
from dataclasses import replace
from types import SimpleNamespace

from config.network import load_network
from consensus.daa import next_difficulty_bits
from consensus.pow import compact_to_target, target_to_compact


class TestDifficultyAdjustment(unittest.TestCase):
    def setUp(self):
        self.network = replace(
            load_network("simnet"),
            daa_window_blocks=16,
            daa_median_time_blocks=5,
        )
        self.genesis = SimpleNamespace(timestamp=0.0, bits=self.network.genesis.bits)

    def chain(self, spacing_seconds, bits=None):
        blocks = [self.genesis]
        timestamp = 0.0
        block_bits = self.network.daa_initial_bits if bits is None else bits
        for _ in range(self.network.daa_window_blocks):
            timestamp += spacing_seconds
            blocks.append(SimpleNamespace(timestamp=timestamp, bits=block_bits))
        return blocks

    def test_bootstrap_uses_manifest_initial_target(self):
        self.assertEqual(
            next_difficulty_bits([self.genesis], self.network),
            self.network.daa_initial_bits,
        )

    def test_fast_blocks_make_target_harder(self):
        bits = next_difficulty_bits(self.chain(0.25), self.network)
        self.assertLess(compact_to_target(bits), compact_to_target(self.network.daa_initial_bits))

    def test_slow_blocks_make_target_easier_but_not_past_pow_limit(self):
        hard_network = replace(self.network, daa_initial_bits=0x1F7FFFFF)
        bits = next_difficulty_bits(self.chain(4.0, hard_network.daa_initial_bits), hard_network)
        self.assertGreater(compact_to_target(bits), compact_to_target(hard_network.daa_initial_bits))
        self.assertLessEqual(compact_to_target(bits), compact_to_target(hard_network.pow_limit_bits))

    def test_same_selected_chain_is_deterministic(self):
        chain = self.chain(0.75)
        self.assertEqual(
            next_difficulty_bits(chain, self.network),
            next_difficulty_bits(list(chain), self.network),
        )

    def test_single_timestamp_outlier_is_absorbed_by_endpoint_median(self):
        baseline = self.chain(1.0)
        attacked = [SimpleNamespace(timestamp=b.timestamp, bits=b.bits) for b in baseline]
        attacked[-1].timestamp += 1_000_000.0
        self.assertEqual(
            next_difficulty_bits(baseline, self.network),
            next_difficulty_bits(attacked, self.network),
        )

    def test_repeated_fast_windows_respect_per_block_adjustment_clamp(self):
        chain = [self.genesis]
        timestamp = 0.0
        previous_target = compact_to_target(self.network.daa_initial_bits)
        for _ in range(96):
            bits = next_difficulty_bits(chain, self.network)
            target = compact_to_target(bits)
            clamped = max(1, previous_target // self.network.daa_max_adjustment_factor)
            canonical_clamp = compact_to_target(target_to_compact(clamped))
            self.assertGreaterEqual(target, canonical_clamp)
            timestamp += 0.10
            chain.append(SimpleNamespace(timestamp=timestamp, bits=bits))
            previous_target = target
        self.assertLess(previous_target, compact_to_target(self.network.daa_initial_bits))

    def test_repeated_slow_windows_never_exceed_pow_limit(self):
        hard_bits = 0x1F7FFFFF
        network = replace(self.network, daa_initial_bits=hard_bits)
        chain = [SimpleNamespace(timestamp=0.0, bits=hard_bits)]
        timestamp = 0.0
        limit = compact_to_target(network.pow_limit_bits)
        for _ in range(96):
            bits = next_difficulty_bits(chain, network)
            self.assertLessEqual(compact_to_target(bits), limit)
            timestamp += 10.0
            chain.append(SimpleNamespace(timestamp=timestamp, bits=bits))
        self.assertGreater(compact_to_target(chain[-1].bits), compact_to_target(hard_bits))


if __name__ == "__main__":
    unittest.main()
