"""Deterministic selected-chain difficulty adjustment for BTC-K."""

from __future__ import annotations

from statistics import median

from consensus.pow import compact_to_target, target_to_compact


def next_difficulty_bits(selected_chain: list, network) -> int:
    """Calculate the next compact target from a bounded blue-chain window.

    Only derived selected-chain data is used, so nodes observing the same DAG
    calculate the same target regardless of block arrival order. Endpoint
    median-time samples limit the influence of a single manipulated timestamp.
    """
    window_size = network.daa_window_blocks
    non_genesis = selected_chain[1:]
    if len(non_genesis) < window_size:
        return network.daa_initial_bits

    window = non_genesis[-window_size:]
    median_size = min(network.daa_median_time_blocks, window_size // 2)
    if median_size % 2 == 0:
        median_size -= 1

    first_time_ms = int(median([block.timestamp for block in window[:median_size]]) * 1000)
    last_time_ms = int(median([block.timestamp for block in window[-median_size:]]) * 1000)
    observed_ms = max(1, last_time_ms - first_time_ms)
    expected_ms = max(
        1,
        (window_size - median_size) * network.daa_target_interval_ms,
    )

    factor = network.daa_max_adjustment_factor
    observed_ms = min(expected_ms * factor, max(expected_ms // factor, observed_ms))

    average_target = sum(compact_to_target(block.bits) for block in window) // len(window)
    proposed = average_target * observed_ms // expected_ms

    parent_target = compact_to_target(window[-1].bits)
    proposed = min(parent_target * factor, max(max(1, parent_target // factor), proposed))
    proposed = min(proposed, compact_to_target(network.pow_limit_bits))

    # Compact encoding discards low bits; round-trip to the canonical value.
    return target_to_compact(proposed)
