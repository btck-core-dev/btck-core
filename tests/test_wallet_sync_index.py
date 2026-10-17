"""Offline wallet synchronization index construction and paging tests."""

import json
import os
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from wallet_sync_index import WalletSyncIndex, WalletSyncIndexError, build_wallet_sync_index


IDENTITY = {
    "network": "devnet",
    "chain_id": "btck-devnet-v3",
    "genesis_hash": "01" * 32,
    "consensus_fingerprint": "02" * 32,
}


class TestWalletSyncIndex(unittest.TestCase):
    def _snapshot(self, path: Path) -> None:
        with sqlite3.connect(path) as db:
            db.executescript(
                """
                CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE blue_order (position INTEGER PRIMARY KEY, block_hash TEXT NOT NULL);
                CREATE TABLE utxos (txid TEXT NOT NULL, vout INTEGER NOT NULL, entry_json TEXT NOT NULL, PRIMARY KEY(txid,vout));
                CREATE TABLE spent_undo (block_hash TEXT NOT NULL, position INTEGER NOT NULL, txid TEXT NOT NULL, vout INTEGER NOT NULL, entry_json TEXT NOT NULL, PRIMARY KEY(block_hash,position));
                CREATE TABLE created_undo (block_hash TEXT NOT NULL, position INTEGER NOT NULL, txid TEXT NOT NULL, vout INTEGER NOT NULL, PRIMARY KEY(block_hash,position));
                """
            )
            db.executemany("INSERT INTO meta VALUES(?,?)", {
                "format_version": "2", **IDENTITY,
            }.items())
            hashes = [f"{height + 10:064x}" for height in range(3)]
            db.executemany("INSERT INTO blue_order VALUES(?,?)", enumerate(hashes))
            current = {
                "amount_satk": 200, "R_ephem_hex": "02" + "11" * 32,
                "P_onetime_hex": "03" + "22" * 32, "block_height": 1,
                "is_coinbase": True, "timelock_until_height": 0, "kind": "miner",
            }
            old = {**current, "amount_satk": 100, "block_height": 0}
            db.execute("INSERT INTO utxos VALUES(?,?,?)", ("bb" * 32, 0, json.dumps(current)))
            db.execute(
                "INSERT INTO spent_undo VALUES(?,?,?,?,?)",
                (hashes[2], 0, "aa" * 32, 0, json.dumps(old)),
            )
            db.execute("INSERT INTO created_undo VALUES(?,?,?,?)", (hashes[0], 0, "aa" * 32, 0))
            db.execute("INSERT INTO created_undo VALUES(?,?,?,?)", (hashes[1], 0, "bb" * 32, 0))

    def test_build_and_page_preserves_created_and_spent_history(self):
        with tempfile.TemporaryDirectory() as root:
            snapshot = Path(root) / "chain.snapshot.v2.sqlite3"
            index_path = Path(root) / "wallet-sync-index.sqlite3"
            self._snapshot(snapshot)
            result = build_wallet_sync_index(snapshot, index_path)
            self.assertEqual(result["tip_height"], "2")
            index = WalletSyncIndex(index_path, IDENTITY)
            first = index.get_page(0, 2, "")
            self.assertEqual([b["height"] for b in first["blocks"]], [0, 1])
            self.assertEqual(first["blocks"][0]["created"][0]["amount_satk"], 100)
            self.assertEqual(first["blocks"][1]["created"][0]["amount_satk"], 200)
            second = index.get_page(2, 2, first["anchor_hash"])
            self.assertEqual(second["blocks"][0]["spent"], [{"txid": "aa" * 32, "vout": 0}])
            self.assertFalse(second["done"])
            self.assertIsNone(index.get_page(3, 2, second["anchor_hash"]))

    def test_stale_cursor_and_wrong_network_are_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            snapshot = Path(root) / "snapshot.sqlite3"
            index_path = Path(root) / "index.sqlite3"
            self._snapshot(snapshot)
            build_wallet_sync_index(snapshot, index_path)
            index = WalletSyncIndex(index_path, IDENTITY)
            with self.assertRaisesRegex(ValueError, "stale"):
                index.get_page(1, 1, "ff" * 32)
            with self.assertRaisesRegex(WalletSyncIndexError, "chain_id"):
                WalletSyncIndex(index_path, {**IDENTITY, "chain_id": "wrong"})

    def test_public_edge_serves_index_without_node_rpc(self):
        import web_explorer_server as explorer

        with tempfile.TemporaryDirectory() as root:
            snapshot = Path(root) / "snapshot.sqlite3"
            index_path = Path(root) / "index.sqlite3"
            self._snapshot(snapshot)
            build_wallet_sync_index(snapshot, index_path)
            previous = explorer._WALLET_SYNC_INDEX
            explorer._WALLET_SYNC_INDEX = WalletSyncIndex(index_path, IDENTITY)
            try:
                with patch.object(
                    explorer.urllib.request, "urlopen",
                    side_effect=AssertionError("historical page touched node RPC"),
                ):
                    page = explorer.fetch_wallet_sync_page(0, 2, "")
                self.assertEqual(page["source"], "static-index")
                self.assertEqual(page["next_height"], 2)
            finally:
                explorer._WALLET_SYNC_INDEX = previous

    def test_static_only_edge_refuses_unverified_live_tail(self):
        import web_explorer_server as explorer

        with tempfile.TemporaryDirectory() as root:
            snapshot = Path(root) / "snapshot.sqlite3"
            index_path = Path(root) / "index.sqlite3"
            self._snapshot(snapshot)
            build_wallet_sync_index(snapshot, index_path)
            previous_index = explorer._WALLET_SYNC_INDEX
            previous_static_only = explorer.WALLET_SYNC_STATIC_ONLY
            explorer._WALLET_SYNC_INDEX = WalletSyncIndex(index_path, IDENTITY)
            explorer.WALLET_SYNC_STATIC_ONLY = True
            try:
                with patch.object(
                    explorer.urllib.request, "urlopen",
                    side_effect=AssertionError("static-only tail touched node RPC"),
                ), self.assertRaisesRegex(WalletSyncIndexError, "static checkpoint"):
                    explorer.fetch_wallet_sync_page(3, 1, f"{12:064x}")
            finally:
                explorer._WALLET_SYNC_INDEX = previous_index
                explorer.WALLET_SYNC_STATIC_ONLY = previous_static_only

    def test_wallet_tip_uses_shared_node_cache_without_rpc(self):
        import web_explorer_server as explorer

        previous_info = explorer._CACHED_NODE_INFO
        previous_time = explorer._LAST_CACHE_TIME
        explorer._CACHED_NODE_INFO = {
            "dag": {"ledger_height": 1234, "selected_tip": "ab" * 32},
        }
        explorer._LAST_CACHE_TIME = time.time()
        try:
            with patch.object(
                explorer.urllib.request, "urlopen",
                side_effect=AssertionError("tip request touched node RPC"),
            ):
                tip = explorer.build_wallet_tip_payload()
            self.assertEqual(tip["format"], "btck-wallet-tip-v1")
            self.assertEqual(tip["tip_height"], 1234)
            self.assertEqual(tip["tip_hash"], "ab" * 32)
        finally:
            explorer._CACHED_NODE_INFO = previous_info
            explorer._LAST_CACHE_TIME = previous_time

    def test_static_history_reports_actual_live_tip_not_old_index_height(self):
        import web_explorer_server as explorer
        from types import SimpleNamespace
        indexed={'format':'btck-wallet-sync-v1','from_height':0,'next_height':3,
                 'tip_height':3,'tip_hash':'ab'*32,'done':False,'blocks':[]}
        index=SimpleNamespace(tip_height=2,get_page=lambda *_args:indexed)
        with patch.object(explorer,'_WALLET_SYNC_INDEX',index),patch.object(
            explorer,'build_wallet_tip_payload',return_value={'tip_height':500,'tip_hash':'cd'*32}):
            result=explorer.fetch_wallet_sync_page(0,1000,'')
        self.assertEqual(result['tip_height'],500)
        self.assertFalse(result['done'])
        self.assertEqual(indexed['tip_height'],3)

    def test_static_index_ahead_of_recovering_node_is_not_served_as_live(self):
        import web_explorer_server as explorer
        from types import SimpleNamespace
        index=SimpleNamespace(tip_height=100,get_page=lambda *_args:{'next_height':10})
        with patch.object(explorer,'_WALLET_SYNC_INDEX',index),patch.object(
            explorer,'build_wallet_tip_payload',return_value={'tip_height':50,'tip_hash':'cd'*32}):
            with self.assertRaisesRegex(WalletSyncIndexError,'ahead'):
                explorer.fetch_wallet_sync_page(0,1000,'')

    def test_wallet_tip_http_route_serves_cached_cursor(self):
        import web_explorer_server as explorer
        from http.server import ThreadingHTTPServer

        previous_info = explorer._CACHED_NODE_INFO
        previous_time = explorer._LAST_CACHE_TIME
        explorer._CACHED_NODE_INFO = {
            "dag": {"ledger_height": 4321, "selected_tip": "ef" * 32},
        }
        explorer._LAST_CACHE_TIME = time.time()
        server = ThreadingHTTPServer(("127.0.0.1", 0), explorer.ExplorerHTTPHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{server.server_port}/api/wallet/tip", timeout=3,
            ) as response:
                tip = json.loads(response.read().decode("utf-8"))
            self.assertEqual(tip["tip_height"], 4321)
            self.assertEqual(tip["tip_hash"], "ef" * 32)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)
            explorer._CACHED_NODE_INFO = previous_info
            explorer._LAST_CACHE_TIME = previous_time

    def test_live_tail_cache_coalesces_repeated_wallet_pages(self):
        import web_explorer_server as explorer

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return json.dumps({
                    "jsonrpc": "2.0", "id": 1,
                    "result": {
                        "format": "btck-wallet-sync-v1",
                        "from_height": 500,
                        "next_height": 500,
                        "anchor_hash": "cd" * 32,
                        "tip_height": 499,
                        "tip_hash": "cd" * 32,
                        "done": True,
                        "blocks": [],
                    },
                }).encode("utf-8")

        previous_index = explorer._WALLET_SYNC_INDEX
        previous_static_only = explorer.WALLET_SYNC_STATIC_ONLY
        explorer._WALLET_SYNC_INDEX = None
        explorer.WALLET_SYNC_STATIC_ONLY = False
        with explorer._WALLET_SYNC_PAGE_CACHE_LOCK:
            explorer._WALLET_SYNC_PAGE_CACHE.clear()
        try:
            with patch.object(
                explorer.urllib.request, "urlopen", return_value=Response(),
            ) as mocked:
                first = explorer.fetch_wallet_sync_page(500, 1000, "cd" * 32)
                second = explorer.fetch_wallet_sync_page(500, 1000, "cd" * 32)
            self.assertEqual(first, second)
            self.assertEqual(mocked.call_count, 1)
        finally:
            explorer._WALLET_SYNC_INDEX = previous_index
            explorer.WALLET_SYNC_STATIC_ONLY = previous_static_only
            with explorer._WALLET_SYNC_PAGE_CACHE_LOCK:
                explorer._WALLET_SYNC_PAGE_CACHE.clear()


    def test_concurrent_live_cache_misses_issue_one_upstream_rpc(self):
        import concurrent.futures
        import threading
        import time
        import web_explorer_server as explorer

        result = {"format": "btck-wallet-sync-v1", "blocks": [], "done": True}
        calls = []
        barrier = threading.Barrier(8)

        class Response:
            def __init__(self, payload): self.payload = payload
            def __enter__(self): return self
            def __exit__(self, *_): return False
            def read(self): return json.dumps(self.payload).encode()

        def upstream(request, **_):
            if request.get_method() != 'POST':
                return Response({})
            calls.append(1)
            time.sleep(.05)
            return Response({"result": result})

        def fetch(_):
            barrier.wait(timeout=3)
            return explorer.fetch_wallet_sync_page(987654, 1, 'fa'*32)

        with patch.object(explorer, '_WALLET_SYNC_INDEX', None), \
             patch.object(explorer, 'WALLET_SYNC_STATIC_ONLY', False), \
             patch.object(explorer.urllib.request, 'urlopen', side_effect=upstream):
            with explorer._WALLET_SYNC_PAGE_CACHE_LOCK:
                explorer._WALLET_SYNC_PAGE_CACHE.clear()
            try:
                with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
                    values = list(pool.map(fetch, range(8)))
                self.assertEqual(len(calls), 1)
                self.assertEqual(values, [result]*8)
                self.assertFalse(explorer._WALLET_SYNC_FETCH_LOCK.locked())
            finally:
                with explorer._WALLET_SYNC_PAGE_CACHE_LOCK:
                    explorer._WALLET_SYNC_PAGE_CACHE.clear()


if __name__ == "__main__":
    unittest.main()
