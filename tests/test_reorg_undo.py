import copy
from dataclasses import asdict
import unittest
from unittest.mock import patch

from consensus.combo_state_machine import ComboStateMachine
from node.blockdag import BlockDAG
from node.utxo_set import UTXOSet, TrackedUTXODict, UTXOEntry
from tests.pow_helpers import mine_block

COINBASE=(bytes.fromhex('02'+'11'*32),bytes.fromhex('03'+'22'*32))


def entry(amount,kind='standard'):
    return UTXOEntry(amount,'','',0,kind=kind)


class ReorgUndoTests(unittest.TestCase):
    def test_bounded_views_match_reference_after_forks_merges_and_rollback(self):
        dag=BlockDAG(network='simnet')
        start=dag.blocks[dag.genesis_hash].timestamp
        tick=0
        def make(parents):
            nonlocal tick
            tick+=1
            return mine_block(dag,parents=parents,timestamp=start+tick,
                              coinbase_output=COINBASE)
        def check():
            self.assertEqual(dag.virtual_selected_parent_chain,
                             dag.get_selected_parent_chain(dag.selected_tip))
            self.assertEqual(dag.virtual_blue_order,dag.get_virtual_blue_order(dag.selected_tip))
            self.assertEqual(dag._canonical_blue_positions(),
                             {h:i for i,h in enumerate(dag.virtual_blue_order)})
        for depth in (1,4,12):
            anchor=dag.selected_tip
            for _ in range(depth): dag.add_block(make([dag.selected_tip]))
            branch=anchor
            for _ in range(depth+2):
                block=make([branch]); branch=block.hash
                before=dag.state_root()
                def fail(_): raise RuntimeError('durable failure')
                with self.assertRaisesRegex(RuntimeError,'durable failure'):
                    dag.add_block(block,commit_hook=fail)
                self.assertEqual(dag.state_root(),before)
                check()
                dag.add_block(block); check()
            dag.add_block(make(dag.get_tips())); check()
            # Snapshot loaders replace canonical list objects. Rebuild only
            # the derived cache; no serialized cache is required.
            dag.virtual_blue_order=list(dag.virtual_blue_order)
            check()

    def test_warmed_shallow_reorg_never_reconstructs_whole_chain(self):
        dag=BlockDAG(network='simnet'); start=dag.blocks[dag.genesis_hash].timestamp
        siblings=[mine_block(dag,parents=[dag.genesis_hash],timestamp=start+i+1,
                             coinbase_output=COINBASE,nonce=100*i) for i in range(2)]
        chosen,side=sorted(siblings,key=lambda b:b.hash,reverse=True)
        dag.add_block(chosen); dag.add_block(side)
        child=mine_block(dag,parents=[side.hash],timestamp=start+4,coinbase_output=COINBASE)
        positions=dag._canonical_blue_positions()
        with patch.object(dag,'get_selected_parent_chain',side_effect=AssertionError('full selected walk')), \
             patch.object(dag,'get_virtual_blue_order',side_effect=AssertionError('full blue walk')):
            dag.add_block(child)
            extension=mine_block(dag,parents=[child.hash],timestamp=start+5,coinbase_output=COINBASE)
            dag.add_block(extension)
        self.assertIs(dag._canonical_blue_positions(),positions)
        self.assertEqual(dag.virtual_blue_order,dag.get_virtual_blue_order(dag.selected_tip))

    def test_empty_reorg_avoids_global_accounting_and_utxo_copy(self):
        class NoAccountingEnumeration(dict):
            def items(self): raise AssertionError('global accounting snapshot')
        class NoUTXOEnumeration(TrackedUTXODict):
            def __iter__(self): raise AssertionError('global UTXO snapshot')
            def keys(self): raise AssertionError('global UTXO snapshot')
        for guarded in ('accounting','utxo'):
            with self.subTest(guarded=guarded):
                dag=BlockDAG(network='simnet'); start=dag.blocks[dag.genesis_hash].timestamp
                siblings=[mine_block(dag,parents=[dag.genesis_hash],timestamp=start+i+1,
                                     coinbase_output=COINBASE,nonce=100*i) for i in range(2)]
                chosen,side=sorted(siblings,key=lambda b:b.hash,reverse=True)
                dag.add_block(chosen); dag.add_block(side)
                child=mine_block(dag,parents=[side.hash],timestamp=start+4,coinbase_output=COINBASE)
                if guarded=='accounting': dag.blocks=NoAccountingEnumeration(dag.blocks)
                else: dag.utxo_set._utxos=NoUTXOEnumeration(dag.utxo_set.utxos)
                self.assertTrue(dag.add_block(child))
                self.assertEqual(dag.issued_supply_sats,dag.utxo_set.total_value_sats())

    def test_tracked_first_writes_restore_values_and_kind_totals(self):
        values=TrackedUTXODict({'old':entry(5),'spent':entry(7,'jackpot_pool')})
        values.begin_undo()
        values['old']=entry(20,'miner'); values['old']=entry(30)
        values.pop('spent'); values['new']=entry(100)
        values.finish_undo(rollback=True)
        self.assertEqual({k:v.amount_satk for k,v in values.items()},{'old':5,'spent':7})
        self.assertEqual(values.total_satk,12)
        self.assertEqual(values.kind_totals,{'standard':5,'jackpot_pool':7})

    def test_clear_is_reversible_and_nested_transaction_refused(self):
        values=TrackedUTXODict({'old':entry(5)})
        values.begin_undo()
        with self.assertRaises(RuntimeError): values.begin_undo()
        values.clear(); values['new']=entry(10)
        values.finish_undo(rollback=True)
        self.assertEqual(list(values),['old']); self.assertEqual(values.total_satk,5)

    def test_plain_undo_mapping_never_enumerates_untouched_history(self):
        from node.utxo_set import UndoMapping
        class NoEnumeration(dict):
            def __iter__(self): raise AssertionError('global copy')
            def keys(self): raise AssertionError('global copy')
            def items(self): raise AssertionError('global copy')
        base=NoEnumeration({'old':[1]})
        wrapped=UndoMapping(base)
        wrapped['old']=[2]; wrapped['new']=[3]; wrapped.pop('old')
        wrapped.rollback()
        self.assertEqual(base['old'],[1]); self.assertNotIn('new',base)

    def test_utxo_overlay_replacement_and_undo_maps_rollback(self):
        utxos=UTXOSet(); utxos.utxos['old']=entry(5)
        original=utxos.utxos
        utxos._block_created_undo['prior']=['old']
        utxos.begin_rollback()
        utxos.utxos['old']=entry(7)
        utxos.utxos={'replacement':entry(20,'miner')}
        utxos._block_created_undo.pop('prior')
        utxos._block_created_undo['next']=['replacement']
        utxos.finish_rollback(rollback=True)
        self.assertIs(utxos.utxos,original)
        self.assertEqual(utxos.total_value_sats(),5)
        self.assertEqual(utxos._block_created_undo,{'prior':['old']})

    def test_successful_overlay_replacement_is_kept(self):
        utxos=UTXOSet(); utxos.utxos['old']=entry(5)
        utxos.begin_rollback(); utxos.utxos={'new':entry(20)}
        utxos._block_created_undo['next']=['new']; utxos.finish_rollback()
        self.assertEqual(list(utxos.utxos),['new'])
        self.assertEqual(utxos.total_value_sats(),20)
        self.assertIsInstance(utxos._block_created_undo,dict)

    def combo(self,last):
        c=ComboStateMachine()
        for h in range(1,last+1): c.process_block(h,f'{h:064x}',500,5)
        return c

    def test_combo_draw_creation_and_candidate_append_are_reversible(self):
        with patch('consensus.combo_state_machine.is_combo_block',side_effect=lambda h:h==3):
            c=self.combo(2); before=copy.deepcopy(c.__dict__)
            c.begin_rollback(); c.process_block(3,f'{3:064x}',500,5); c.process_block(4,f'{4:064x}',500,5)
            c.finish_rollback(rollback=True)
            self.assertEqual(c.__dict__,before)

    def test_combo_beacon_and_revert_across_draw_are_reversible(self):
        with patch('consensus.combo_state_machine.is_combo_block',side_effect=lambda h:h==3):
            c=self.combo(102); before=copy.deepcopy(c.__dict__)
            c.begin_rollback(); resolution,_=c.process_block(103,f'{103:064x}',500,5)
            self.assertIsNotNone(resolution); c.finish_rollback(rollback=True)
            self.assertEqual(c.__dict__,before)
            c.process_block(103,f'{103:064x}',500,5); before=copy.deepcopy(c.__dict__)
            c.begin_rollback(); c.revert_to_height(2); c.finish_rollback(rollback=True)
            self.assertEqual(c.__dict__,before)

    def test_reorg_commit_failure_restores_root_and_all_undo_maps(self):
        dag=BlockDAG(network='simnet'); start=dag.blocks[dag.genesis_hash].timestamp
        siblings=[mine_block(dag,parents=[dag.genesis_hash],timestamp=start+i+1,
                            coinbase_output=COINBASE,nonce=100*i) for i in range(2)]
        chosen,side=sorted(siblings,key=lambda b:b.hash,reverse=True)
        dag.add_block(chosen); dag.add_block(side)
        child=mine_block(dag,parents=[side.hash],timestamp=start+4,coinbase_output=COINBASE)
        before=dag.state_root()
        undo=copy.deepcopy((dag.utxo_set._block_spent_undo,dag.utxo_set._block_created_undo,
                            dag.utxo_set._block_fees,dag.utxo_set._block_accepted_txids))
        def fail(_): raise RuntimeError('durable failure')
        with self.assertRaisesRegex(RuntimeError,'durable failure'): dag.add_block(child,commit_hook=fail)
        self.assertEqual(dag.state_root(),before)
        self.assertEqual((dag.utxo_set._block_spent_undo,dag.utxo_set._block_created_undo,
                          dag.utxo_set._block_fees,dag.utxo_set._block_accepted_txids),undo)
        self.assertIsNone(dag._reorg_accounting_undo)
        self.assertIsNone(dag.utxo_set._rollback_state)
        self.assertIsNone(dag.combo_machine._rollback_state)
        self.assertTrue(dag.add_block(child))
        self.assertEqual(dag.issued_supply_sats,dag.utxo_set.total_value_sats())
