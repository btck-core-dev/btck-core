import asyncio
import os
import sys
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from node.rpc_client import HTTPNodeRPCClient, RPCError


class TestHTTPNodeRPCClient(unittest.IsolatedAsyncioTestCase):
    async def test_call_has_a_hard_timeout(self):
        client = HTTPNodeRPCClient("http://127.0.0.1:16110/rpc")

        async def never_returns(method, params):
            await asyncio.sleep(1.0)

        with patch.object(client, "_call", AsyncMock(side_effect=never_returns)):
            with self.assertRaisesRegex(RPCError, "timed out"):
                await client.call("getInfo", timeout=0.01)


if __name__ == "__main__":
    unittest.main()
