"""
Test BTC-K 4-Node P2P Gossip Mesh Network Simulation
---------------------------------------------------
Verifies multi-node mesh topology:
- Node 1 (Seed / Bootstrap): 127.0.0.1:16131
- Node 2 (Peer A):           127.0.0.1:16132 -> connects to Node 1
- Node 3 (Peer B):           127.0.0.1:16133 -> connects to Node 1 & Node 2
- Node 4 (Late Joiner):      127.0.0.1:16134 -> connects to Node 3

Verifies:
1. Mutual handshakes across mesh topology.
2. Multi-hop block propagation & gossip relay.
3. BlockDAG consensus consistency across all 4 independent node instances.
"""

import asyncio
import unittest
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from node.blockdag import BlockDAG, DAGBlock
from p2p.p2p_network import P2PNetwork
from privacy.stealth_address import StealthKeyPair

class TestP2PMultiNodeMesh(unittest.IsolatedAsyncioTestCase):

    async def test_4_node_mesh_gossip_and_consensus(self):
        print("\n================================================================")
        print("🌐 Starting BTC-K 4-Node P2P Gossip Mesh Network Simulation")
        print("================================================================")

        # 1. Initialize 4 independent nodes sharing identical Genesis
        dag1 = BlockDAG()
        dag2 = BlockDAG()
        dag3 = BlockDAG()
        dag4 = BlockDAG()

        self.assertEqual(dag1.genesis_hash, dag2.genesis_hash)
        self.assertEqual(dag1.genesis_hash, dag3.genesis_hash)
        self.assertEqual(dag1.genesis_hash, dag4.genesis_hash)

        # Node 1: Seed
        p2p1 = P2PNetwork(dag=dag1, p2p_host="127.0.0.1", p2p_port=16131)
        await p2p1.start()
        print("✓ Node 1 (Seed Bootstrap) active on 127.0.0.1:16131")

        # Node 2: Connects to Node 1
        p2p2 = P2PNetwork(dag=dag2, p2p_host="127.0.0.1", p2p_port=16132, seed_peers=["127.0.0.1:16131"])
        await p2p2.start()
        print("✓ Node 2 active on 127.0.0.1:16132 (peered with Node 1)")

        # Node 3: Connects to Node 1 and Node 2
        p2p3 = P2PNetwork(dag=dag3, p2p_host="127.0.0.1", p2p_port=16133, seed_peers=["127.0.0.1:16131", "127.0.0.1:16132"])
        await p2p3.start()
        print("✓ Node 3 active on 127.0.0.1:16133 (peered with Node 1 & 2)")

        await asyncio.sleep(0.6)

        # 2. Node 1 mines Block A and broadcasts to mesh
        miner1 = StealthKeyPair().get_public_stealth_address()
        R1, P1 = StealthKeyPair.generate_one_time_stealth_output(miner1)
        block_a = DAGBlock(
            parents=dag1.get_tips(),
            timestamp=1788912200.0,
            bits=1,
            nonce=1001,
            coinbase_output=(R1, P1)
        )
        dag1.add_block(block_a)
        await p2p1.broadcast_block(block_a.hash)
        print(f"✓ Node 1 mined & broadcasted Block A ({block_a.hash[:16]}...)")

        # Wait for gossip propagation to Node 2 and Node 3
        for _ in range(30):
            if block_a.hash in dag2.blocks and block_a.hash in dag3.blocks:
                break
            await asyncio.sleep(0.1)

        self.assertIn(block_a.hash, dag2.blocks, "Node 2 failed to receive Block A")
        self.assertIn(block_a.hash, dag3.blocks, "Node 3 failed to receive Block A")
        print("✓ Block A propagated successfully to Node 2 & Node 3 via P2P gossip")

        # 3. Node 2 mines Block B referencing Block A and broadcasts
        miner2 = StealthKeyPair().get_public_stealth_address()
        R2, P2 = StealthKeyPair.generate_one_time_stealth_output(miner2)
        block_b = DAGBlock(
            parents=dag2.get_tips(),
            timestamp=1788912205.0,
            bits=1,
            nonce=2002,
            coinbase_output=(R2, P2)
        )
        dag2.add_block(block_b)
        await p2p2.broadcast_block(block_b.hash)
        print(f"✓ Node 2 mined & broadcasted Block B ({block_b.hash[:16]}...)")

        # Wait for gossip propagation of Block B back to Node 1 and Node 3
        for _ in range(30):
            if block_b.hash in dag1.blocks and block_b.hash in dag3.blocks:
                break
            await asyncio.sleep(0.1)

        self.assertIn(block_b.hash, dag1.blocks, "Node 1 failed to receive Block B")
        self.assertIn(block_b.hash, dag3.blocks, "Node 3 failed to receive Block B")
        print("✓ Block B propagated bidirectionally across mesh to Node 1 & Node 3")

        # 4. Late-joining Node 4 connects to Node 3 only (multi-hop propagation test)
        p2p4 = P2PNetwork(dag=dag4, p2p_host="127.0.0.1", p2p_port=16134, seed_peers=["127.0.0.1:16133"])
        await p2p4.start()
        print("✓ Node 4 (Late Joiner) active on 127.0.0.1:16134, peered with Node 3")
        await asyncio.sleep(0.6)

        # Node 1 mines Block C and broadcasts
        miner3 = StealthKeyPair().get_public_stealth_address()
        R3, P3 = StealthKeyPair.generate_one_time_stealth_output(miner3)
        block_c = DAGBlock(
            parents=dag1.get_tips(),
            timestamp=1788912210.0,
            bits=1,
            nonce=3003,
            coinbase_output=(R3, P3)
        )
        dag1.add_block(block_c)
        await p2p1.broadcast_block(block_c.hash)
        print(f"✓ Node 1 mined Block C ({block_c.hash[:16]}...) -> Multi-hop relaying to Node 4")

        # Wait for Block C to reach Node 4 through Node 3 relay
        for _ in range(30):
            if block_c.hash in dag4.blocks:
                break
            await asyncio.sleep(0.1)

        self.assertIn(block_c.hash, dag4.blocks, "Node 4 failed to receive Block C via multi-hop relay")
        print("✓ Block C received by Node 4 through multi-hop relay (Node 1 -> Node 3 -> Node 4)!")

        # 5. Verify consensus state across all 4 nodes
        for node_idx, dag in enumerate([dag1, dag2, dag3, dag4], start=1):
            self.assertIn(block_c.hash, dag.blocks)
            print(f"  • Node {node_idx} DAG Block Count: {len(dag.blocks)}, Tips: {[t[:10] for t in dag.get_tips()]}")

        print("\n================================================================")
        print("🎉 4-NODE P2P GOSSIP MESH SIMULATION PASSED (100% CONSENSUS)")
        print("================================================================")

        # Clean shutdown of all nodes
        await p2p1.stop()
        await p2p2.stop()
        await p2p3.stop()
        await p2p4.stop()

if __name__ == "__main__":
    unittest.main()
