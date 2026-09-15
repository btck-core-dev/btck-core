"""
Test BTC-K P2P Gossip Synchronization between 2 Nodes
------------------------------------------------------
Verifies mutual handshake, genesis compatibility checking,
INV broadcast, and BlockDAG synchronization via unittest.
"""

import asyncio
import unittest
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from node.blockdag import BlockDAG, DAGBlock
from p2p.p2p_network import P2PNetwork
from privacy.stealth_address import StealthKeyPair

class TestP2PGossip(unittest.IsolatedAsyncioTestCase):

    async def test_p2p_handshake_and_block_sync(self):
        # 1. Initialize Node A and Node B with identical Genesis Block
        dag_a = BlockDAG()
        dag_b = BlockDAG()
        self.assertEqual(dag_a.genesis_hash, dag_b.genesis_hash)

        # Start Node A P2P on 16125
        p2p_a = P2PNetwork(dag=dag_a, p2p_host="127.0.0.1", p2p_port=16125)
        await p2p_a.start()

        # Start Node B P2P on 16126, connecting to Node A
        p2p_b = P2PNetwork(dag=dag_b, p2p_host="127.0.0.1", p2p_port=16126, seed_peers=["127.0.0.1:16125"])
        await p2p_b.start()
        await asyncio.sleep(0.5)

        # 2. Mine a block on Node A
        miner_addr = StealthKeyPair().get_public_stealth_address()
        R_ephem, P_out = StealthKeyPair.generate_one_time_stealth_output(miner_addr)
        
        new_blk = DAGBlock(
            parents=dag_a.get_tips(),
            timestamp=1788912100.0,
            bits=1,
            nonce=9999,
            coinbase_output=(R_ephem, P_out)
        )
        dag_a.add_block(new_blk)

        # 3. Node A broadcasts block via P2P
        await p2p_a.broadcast_block(new_blk.hash)
        
        # Wait for gossip and sync to Node B
        for _ in range(30):
            if new_blk.hash in dag_b.blocks:
                break
            await asyncio.sleep(0.1)

        self.assertIn(new_blk.hash, dag_b.blocks)
        synced_blk = dag_b.blocks[new_blk.hash]
        self.assertEqual(synced_blk.reward_sats, new_blk.reward_sats)

        await p2p_a.stop()
        await p2p_b.stop()

if __name__ == "__main__":
    unittest.main()
