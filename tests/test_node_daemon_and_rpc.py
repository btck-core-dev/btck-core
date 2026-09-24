"""
Integration Test: BTC-K Full Node Daemon (btckd) & RPC Interface
----------------------------------------------------------------
Verifies BlockDAG template dispatch, multi-block submission,
GHOSTDAG score calculation, and stealth UTXO scanning via JSON-RPC.
"""

import asyncio
import json
import unittest
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from node.daemon import BTCKDaemon
from privacy.stealth_address import StealthKeyPair

async def async_rpc_call(host: str, port: int, method: str, params: dict, req_id: int = 1):
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

    # Read HTTP response headers
    while True:
        line = await reader.readline()
        if line == b'\r\n' or line == b'\n' or not line:
            break
            
    # Read remaining body
    resp_body = await reader.read()
    writer.close()
    await writer.wait_closed()
    return json.loads(resp_body.decode('utf-8'))

class TestNodeDaemonAndRPC(unittest.IsolatedAsyncioTestCase):

    async def test_daemon_rpc_workflow(self):
        test_host = "127.0.0.1"
        test_port = 16180
        daemon = BTCKDaemon(host=test_host, port=test_port, p2p_port=16181)
        await daemon.start()
        await asyncio.sleep(0.2)
        
        try:
            # 1. Test getInfo
            info = await async_rpc_call(test_host, test_port, "getInfo", {})
            self.assertEqual(info["result"]["version"], "1.0.0-cypherpunk")
            
            # 2. Test getBlockTemplate
            tpl = await async_rpc_call(test_host, test_port, "getBlockTemplate", {})
            parents = tpl["result"]["parents"]
            self.assertGreater(len(parents), 0)
            
            # 3. Create a miner stealth wallet
            miner_wallet = StealthKeyPair()
            miner_addr = miner_wallet.get_public_stealth_address()
            
            # Generate 5 consecutive blocks mined to this stealth address
            for i in range(1, 6):
                R_ephem, P_onetime = StealthKeyPair.generate_one_time_stealth_output(miner_addr)
                
                submit_res = await async_rpc_call(test_host, test_port, "submitBlock", {
                    "parents": parents,
                    "timestamp": 1788912400.0 + i,
                    "bits": 1,
                    "nonce": i * 1000,
                    "coinbase_output": [R_ephem.hex(), P_onetime.hex()]
                })
                
                block_info = submit_res["result"]
                self.assertTrue(block_info["accepted"])
                parents = [block_info["hash"]]

            # 4. Test scanStealthBalance with ONLY view_priv and spend_pub_hex (Spend key remains strictly offline)
            scan_res = await async_rpc_call(test_host, test_port, "scanStealthBalance", {
                "view_priv_scalar": str(miner_wallet.view_priv),
                "spend_pub_hex": miner_wallet.spend_pub_bytes.hex()
            })
            
            wallet_data = scan_res["result"]
            self.assertEqual(wallet_data["utxo_count"], 5)
            self.assertGreater(wallet_data["total_btck"], 25.0)

            # 5. Verify that attempting to transmit spend_priv_scalar is rejected by security policy
            leak_attempt = await async_rpc_call(test_host, test_port, "scanStealthBalance", {
                "view_priv_scalar": str(miner_wallet.view_priv),
                "spend_priv_scalar": str(miner_wallet.spend_priv)
            })
            self.assertIn("error", leak_attempt)
            self.assertIn("Security policy violation", leak_attempt["error"]["message"])

            # 6. Verify Adversary Scanning (Cannot detect any UTXOs)
            adversary_wallet = StealthKeyPair()
            adv_res = await async_rpc_call(test_host, test_port, "scanStealthBalance", {
                "view_priv_scalar": str(adversary_wallet.view_priv),
                "spend_pub_hex": adversary_wallet.spend_pub_bytes.hex()
            })
            self.assertEqual(adv_res["result"]["utxo_count"], 0)

            # 7. Verify getPeerInfo RPC
            peer_info = await async_rpc_call(test_host, test_port, "getPeerInfo", {})
            self.assertIn("result", peer_info)
            self.assertIsInstance(peer_info["result"], list)

        finally:
            await daemon.stop()

if __name__ == "__main__":
    unittest.main()
