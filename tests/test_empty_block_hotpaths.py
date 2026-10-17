"""No node/replay: empty blocks must not traverse lifetime state."""
from collections.abc import Mapping
from types import SimpleNamespace
import unittest
from unittest.mock import Mock
import importlib.util
import os
from pathlib import Path
import sys

from node.daemon import BTCKDaemon
from node.utxo_set import UTXOSet

# Optional exact LIVE-source baseline, not a reconstructed mock. Expected to
# fail the two traversal guards while preserving nonempty-block semantics.
baseline=os.getenv('BTCK_EMPTY_BLOCK_BASELINE_DIR')
if baseline:
    def load(name,filename):
        spec=importlib.util.spec_from_file_location(name,Path(baseline)/filename)
        module=importlib.util.module_from_spec(spec);sys.modules[name]=module
        spec.loader.exec_module(module)
        return module
    BTCKDaemon=load('btck_live_daemon_baseline','daemon-live.py').BTCKDaemon
    UTXOSet=load('btck_live_utxo_baseline','utxo_set-live.py').UTXOSet

class UntouchableHistory(Mapping):
    def __len__(self):return 1_500_000
    def __iter__(self):raise AssertionError('lifetime history was traversed')
    def __getitem__(self,key):raise AssertionError('lifetime history was read')

class EmptyBlockHotpathTests(unittest.TestCase):
    def test_empty_fee_preview_never_reads_or_copies_large_utxo_map(self):
        utxos=UTXOSet();utxos._utxos=UntouchableHistory()
        for skipped in (False,True):
            self.assertEqual(utxos.preview_block_fees([],480000,skipped),0)

    def test_empty_block_mempool_cleanup_does_not_scan_selected_chain(self):
        daemon=BTCKDaemon.__new__(BTCKDaemon)
        daemon.chain_store=None
        daemon.dag=SimpleNamespace(add_block=Mock(return_value=True),
                                  virtual_selected_parent_chain=UntouchableHistory())
        daemon.mempool={};daemon._revalidate_mempool=Mock()
        block=SimpleNamespace(hash='ab'*32,transactions=[])
        self.assertTrue(daemon.accept_block(block))
        daemon._revalidate_mempool.assert_called_once()

    def test_nonempty_block_retains_existing_mempool_removal_and_revalidation(self):
        daemon=BTCKDaemon.__new__(BTCKDaemon);daemon.chain_store=None
        daemon.dag=SimpleNamespace(add_block=Mock(return_value=True),virtual_selected_parent_chain=['ab'*32])
        daemon.mempool={'payment':object(),'other':object()};daemon._revalidate_mempool=Mock()
        self.assertTrue(daemon.accept_block(SimpleNamespace(hash='ab'*32,transactions=[SimpleNamespace(txid='payment')])))
        self.assertEqual(set(daemon.mempool),{'other'})
        daemon._revalidate_mempool.assert_called_once()
