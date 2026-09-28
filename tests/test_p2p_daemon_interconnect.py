"""
BTC-K Multi-Node P2P Daemon Interconnect & Real-time Gossip Consensus Test
--------------------------------------------------------------------------
Validates Gap 3 (多节点 P2P 联调):
1. Multi-daemon P2P bootstrap & mutual handshake.
2. Real-time block propagation from Node 1 -> Node 2 over P2P TCP wire protocol.
3. Bidirectional block propagation from Node 2 -> Node 1.
4. Late-joining node Initial Block Download (IBD) synchronization.
5. Strict consensus tip & blue score alignment across all independent daemon instances.
"""

import asyncio
import json
import unittest
import sys
import os
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from node.daemon import BTCKDaemon
from privacy.stealth_address import StealthKeyPair
from tests.pow_helpers import block_rpc_params, mine_block

async def rpc_call(host: str, port: int, method: str, params: dict = None, req_id: int = 1):
    params = params or {}
    reader, writer = await asyncio.open_connection(host, port)
    body = json.dumps({"jsonrpc": "2.0", "method": method, "params": params, "id": req_id}).encode('utf-8')
    req_header = (
        f"POST /rpc HTTP/1.1\r\n"
        f"Host: {host}:{port}\r\n"
        f"Content-Type: application/json\r\n"
        f"Content-Length: {len(body)}\r\n"
        f"Connection: close\r\n\r\n"
    ).encode('utf-8')
    
    writer.write(req_header + body)
    await writer.drain()

    while True:
        line = await reader.readline()
        if line in (b'\r\n', b'\n', b''):
            break
            
    resp_body = await reader.read()
    writer.close()
    await writer.wait_closed()
    return json.loads(resp_body.decode('utf-8'))

class TestP2PDaemonInterconnect(unittest.IsolatedAsyncioTestCase):

    async def test_multi_daemon_p2p_consensus_sync(self):
        print("\n" + "=" * 70)
        print("🌐 BTC-K Multi-Daemon P2P Interconnect & Consensus Test (Gap 3)")
        print("=" * 70)

        host = "127.0.0.1"
        # Node 1: Seed / Bootstrap node
        rpc1_port, p2p1_port = 16210, 16211
        daemon1 = BTCKDaemon(host=host, port=rpc1_port, p2p_port=p2p1_port)
        await daemon1.start()
        print(f"✓ Node 1 started (RPC: {rpc1_port}, P2P: {p2p1_port})")

        # Node 2: Connects to Node 1 via seed_peers
        rpc2_port, p2p2_port = 16220, 16221
        daemon2 = BTCKDaemon(host=host, port=rpc2_port, p2p_port=p2p2_port, seed_peers=[f"{host}:{p2p1_port}"])
        await daemon2.start()
        print(f"✓ Node 2 started (RPC: {rpc2_port}, P2P: {p2p2_port}, Seed: {host}:{p2p1_port})")

        daemon3 = None

        try:
            # 1. Allow handshake to settle
            await asyncio.sleep(0.5)

            # Verify peer connection on both nodes
            peer_info1 = await rpc_call(host, rpc1_port, "getPeerInfo")
            peer_info2 = await rpc_call(host, rpc2_port, "getPeerInfo")
            
            self.assertGreaterEqual(len(peer_info1["result"]), 1, "Node 1 has no connected peers")
            self.assertGreaterEqual(len(peer_info2["result"]), 1, "Node 2 has no connected peers")
            print(f"✓ Mutual P2P Handshake verified: Node 1 peers={len(peer_info1['result'])}, Node 2 peers={len(peer_info2['result'])}")

            # 2. Mine Block 1 on Node 1 via RPC submitBlock
            tpl1 = await rpc_call(host, rpc1_port, "getBlockTemplate")
            parents1 = tpl1["result"]["parents"]
            
            miner1 = StealthKeyPair()
            addr1 = miner1.get_public_stealth_address()
            R1, P1 = StealthKeyPair.generate_one_time_stealth_output(addr1)

            candidate1 = mine_block(
                daemon1.dag,
                parents=parents1,
                timestamp=time.time(),
                nonce=88001,
                coinbase_output=(R1, P1),
            )
            submit1 = await rpc_call(
                host, rpc1_port, "submitBlock", block_rpc_params(candidate1)
            )
            block1_hash = submit1["result"]["hash"]
            print(f"✓ Node 1 mined & submitted Block #{submit1['result']['blue_score']} ({block1_hash[:16]}...)")

            # 3. Wait for Node 2 to receive Block 1 via P2P Gossip
            synced = False
            for _ in range(40):
                dag2_info = await rpc_call(host, rpc2_port, "getBlockDagInfo")
                if block1_hash in dag2_info["result"]["tips"]:
                    synced = True
                    break
                await asyncio.sleep(0.1)

            self.assertTrue(synced, "Node 2 failed to synchronize Block 1 from Node 1 via P2P")
            print(f"✓ Node 2 received and accepted Block 1 via P2P Gossip (Tips match Node 1)")

            # 4. Bidirectional test: Mine Block 2 on Node 2 and verify Node 1 receives it
            tpl2 = await rpc_call(host, rpc2_port, "getBlockTemplate")
            parents2 = tpl2["result"]["parents"]
            self.assertIn(block1_hash, parents2, "Block 1 must be parent for Block 2")

            miner2 = StealthKeyPair()
            addr2 = miner2.get_public_stealth_address()
            R2, P2 = StealthKeyPair.generate_one_time_stealth_output(addr2)

            candidate2 = mine_block(
                daemon2.dag,
                parents=parents2,
                timestamp=time.time(),
                nonce=88002,
                coinbase_output=(R2, P2),
            )
            submit2 = await rpc_call(
                host, rpc2_port, "submitBlock", block_rpc_params(candidate2)
            )
            block2_hash = submit2["result"]["hash"]
            print(f"✓ Node 2 mined & submitted Block #{submit2['result']['blue_score']} ({block2_hash[:16]}...)")

            # Wait for Node 1 to receive Block 2
            synced_reverse = False
            for _ in range(40):
                dag1_info = await rpc_call(host, rpc1_port, "getBlockDagInfo")
                if block2_hash in dag1_info["result"]["tips"]:
                    synced_reverse = True
                    break
                await asyncio.sleep(0.1)

            self.assertTrue(synced_reverse, "Node 1 failed to receive Block 2 from Node 2 via P2P reverse gossip")
            print(f"✓ Node 1 synchronized Block 2 from Node 2 (Bidirectional Gossip Verified)")

            # 5. Late Joiner / IBD Test: Node 3 joins after blocks exist
            rpc3_port, p2p3_port = 16230, 16231
            daemon3 = BTCKDaemon(host=host, port=rpc3_port, p2p_port=p2p3_port, seed_peers=[f"{host}:{p2p1_port}"])
            await daemon3.start()
            print(f"✓ Node 3 (Late Joiner) started (RPC: {rpc3_port}, P2P: {p2p3_port})")

            # Wait for Node 3 to complete IBD with Node 1
            ibd_synced = False
            for _ in range(50):
                dag3_info = await rpc_call(host, rpc3_port, "getBlockDagInfo")
                if block2_hash in dag3_info["result"]["tips"] and dag3_info["result"]["total_blocks"] >= 3:
                    ibd_synced = True
                    break
                await asyncio.sleep(0.1)

            self.assertTrue(ibd_synced, "Node 3 failed to catch up via Initial Block Download (IBD)")
            print(f"✓ Node 3 successfully completed IBD catchup: Block count = {dag3_info['result']['total_blocks']}")

            # 6. Verify 100% DAG consensus equivalence across all three nodes
            d1_info = (await rpc_call(host, rpc1_port, "getBlockDagInfo"))["result"]
            d2_info = (await rpc_call(host, rpc2_port, "getBlockDagInfo"))["result"]
            d3_info = (await rpc_call(host, rpc3_port, "getBlockDagInfo"))["result"]

            self.assertEqual(d1_info["total_blocks"], d2_info["total_blocks"])
            self.assertEqual(d2_info["total_blocks"], d3_info["total_blocks"])
            self.assertEqual(d1_info["tips"], d2_info["tips"])
            self.assertEqual(d2_info["tips"], d3_info["tips"])
            self.assertEqual(d1_info["selected_tip"], d3_info["selected_tip"])

            print(f"✓ All 3 nodes converged on identical tip: {d1_info['selected_tip'][:16]}... (Score: {d1_info['max_blue_score']})")
            print("=" * 70)
            print("🎉 GAP 3 MULTI-NODE P2P GOSSIP & CONSENSUS INTERCONNECT PASSED 100%")
            print("=" * 70)

        finally:
            await daemon1.stop()
            await daemon2.stop()
            if daemon3:
                await daemon3.stop()

if __name__ == "__main__":
    unittest.main()
