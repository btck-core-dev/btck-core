"""Test-only helpers that perform real low-difficulty dev/test PoW."""

from consensus.genesis import build_pre_pow_hash
from consensus.kheavyhash import calculate_pow_value, generate_matrix, hash_to_target_int
from consensus.pow import compact_to_target
from node.blockdag import DAGBlock, transaction_commitment


def mine_block(
    dag,
    *,
    parents,
    timestamp,
    coinbase_output,
    transactions=None,
    nonce=0,
):
    """Find a valid nonce under the selected network's easy non-value target."""
    bits = dag.expected_bits(parents)
    txs = transactions or []
    payload_hash = transaction_commitment(txs)
    seed = build_pre_pow_hash(
        dag.network.block_version,
        dag.network.chain_id,
        parents,
        timestamp,
        bits,
        coinbase_output,
        payload_hash,
    )
    matrix = generate_matrix(seed)
    target = compact_to_target(bits)
    timestamp_ms = int(timestamp * 1000)
    for candidate_nonce in range(nonce, nonce + 1_000_000):
        value = calculate_pow_value(seed, timestamp_ms, candidate_nonce, matrix)
        if hash_to_target_int(value) <= target:
            return DAGBlock(
                parents=parents,
                timestamp=timestamp,
                bits=bits,
                nonce=candidate_nonce,
                coinbase_output=coinbase_output,
                transactions=txs,
                version=dag.network.block_version,
                chain_id=dag.network.chain_id,
            )
    raise AssertionError("Could not find test proof of work within 100,000 nonces")


def block_rpc_params(block):
    return {
        "version": block.version,
        "parents": block.parents,
        "timestamp": block.timestamp,
        "bits": block.bits,
        "nonce": block.nonce,
        "coinbase_output": [
            block.coinbase_output[0].hex(),
            block.coinbase_output[1].hex(),
        ],
    }
