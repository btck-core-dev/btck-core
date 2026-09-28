"""Test-only helpers that perform real low-difficulty dev/test PoW."""

from consensus.pow import hash_meets_target
from node.blockdag import DAGBlock


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
    for candidate_nonce in range(nonce, nonce + 100_000):
        block = DAGBlock(
            parents=parents,
            timestamp=timestamp,
            bits=dag.network.pow_limit_bits,
            nonce=candidate_nonce,
            coinbase_output=coinbase_output,
            transactions=transactions,
            version=dag.network.block_version,
            chain_id=dag.network.chain_id,
        )
        if hash_meets_target(
            bytes.fromhex(block.hash), block.bits, dag.network.pow_limit_bits
        ):
            return block
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
