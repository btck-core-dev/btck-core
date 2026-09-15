"""
BTC-K High-Speed BlockDAG Engine (GHOSTDAG Consensus & Virtual Chain Issuance)
-----------------------------------------------------------------------------
1.0 Second BlockDAG architecture:
1. Multi-parent DAG topologies with GHOSTDAG linear ordering.
2. Virtual Selected Parent Chain single-path issuance (P0-01):
   Strictly prevents parallel / red blocks from duplicate currency minting.
3. Canonical Block Header serialization aligned with Genesis (P0-04).
4. Full integration with Minimal Consensus UTXO Set (P0-06).
"""

import hashlib
import time
from typing import List, Dict, Set, Optional, Tuple
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from consensus.kheavyhash import kheavyhash
from consensus.genesis import serialize_block_header, generate_genesis_block, GENESIS_HASH
from tokenomics.emission_schedule import (
    get_base_block_reward_sats,
    is_combo_block,
    SATS_PER_COIN,
    COMBO_MULTIPLIER,
    compute_block_issuance,
    BlockIssuanceResult,
    TOTAL_HARD_CAP_SATK
)
from node.utxo_set import UTXOSet
from privacy.wallet_engine import StealthTransaction
from consensus.combo_state_machine import ComboStateMachine

class DAGBlock:
    def __init__(
        self,
        parents: List[str],
        timestamp: float,
        bits: int,
        nonce: int,
        coinbase_output: Tuple[bytes, bytes], # (R_ephem, P_onetime)
        transactions: Optional[List[StealthTransaction]] = None,
        version: int = 1,
        blue_score: int = 0,
        payload_hash: bytes = b""
    ):
        self.version = version
        self.parents = parents # List of parent block hashes
        self.timestamp = timestamp
        self.bits = bits
        self.nonce = nonce
        self.blue_score = blue_score
        self.coinbase_output = coinbase_output
        self.transactions: List[StealthTransaction] = transactions or []
        self.payload_hash = payload_hash
        
        # GHOSTDAG topological fields
        self.blue_work: int = 0
        self.selected_parent: Optional[str] = None
        self.merge_set: List[str] = []
        self.is_blue: bool = True
        self.is_combo: bool = False
        self.minted_sats: int = 0
        self.reward_sats: int = 0
        self.miner_base_sats: int = 0
        self.pool_deposit_sats: int = 0
        self.combo_payout_sats: int = 0
        
        # Calculate Block Hash with canonical serialization
        self.hash = self.compute_hash()

    def compute_hash(self) -> str:
        # If payload_hash is empty, calculate tx merkle root
        tx_hash = self.payload_hash
        if not tx_hash and self.transactions:
            combined_txids = "".join(tx.txid for tx in self.transactions).encode('utf-8')
            tx_hash = hashlib.sha256(combined_txids).digest()

        header_payload = serialize_block_header(
            version=self.version,
            parents=self.parents,
            timestamp=self.timestamp,
            bits=self.bits,
            nonce=self.nonce,
            blue_score=self.blue_score,
            coinbase_output=self.coinbase_output,
            payload_hash=tx_hash
        )
        return kheavyhash(header_payload).hex()

class BlockDAG:
    """
    Persistent GHOSTDAG graph maintaining DAG tips,
    virtual selected chain, single-path issuance, and UTXO consensus state.
    """
    def __init__(self, k_cluster: int = 18):
        self.k_cluster = k_cluster # GHOSTDAG K-parameter
        self.blocks: Dict[str, DAGBlock] = {}
        self.tips: Set[str] = set()
        self.selected_tip: Optional[str] = None
        self.genesis_hash: str = ""
        self.issued_supply_sats: int = 0
        self.pool_balance_sats: int = 0
        self.virtual_selected_parent_chain: List[str] = []
        self.utxo_set = UTXOSet()
        self.combo_machine = ComboStateMachine()
        
        self._init_genesis()

    def _init_genesis(self):
        """Construct the Immutable Genesis Block #0 with cypherpunk headline"""
        g = generate_genesis_block()
        
        genesis_cb = (bytes.fromhex(g["coinbase_output"][0]), bytes.fromhex(g["coinbase_output"][1]))
        headline_hash = bytes.fromhex(g["headline_hash"])
        genesis = DAGBlock(
            parents=[],
            timestamp=g["timestamp"],
            bits=g["bits"],
            nonce=int(g["nonce"], 16),
            coinbase_output=genesis_cb,
            version=g.get("version", 1),
            blue_score=0,
            payload_hash=headline_hash
        )
        genesis.blue_score = 0
        genesis.blue_work = 1
        genesis.is_blue = True
        genesis.reward_sats = g["reward_sats"]
        genesis.minted_sats = 0
        
        assert genesis.hash == g["hash"] == GENESIS_HASH, f"Genesis hash mismatch: {genesis.hash} != {GENESIS_HASH}"
        
        self.blocks[genesis.hash] = genesis
        self.tips.add(genesis.hash)
        self.selected_tip = genesis.hash
        self.genesis_hash = genesis.hash
        self.virtual_selected_parent_chain = [genesis.hash]

        # Apply genesis to UTXO set (reward is 0 for genesis)
        self.utxo_set.apply_block(
            block_hash=genesis.hash,
            block_height=0,
            coinbase_output=genesis.coinbase_output,
            reward_sats=0,
            transactions=[]
        )

    def get_selected_parent_chain(self, tip_hash: str) -> List[str]:
        """Trace selected parents back to Genesis and return ordered list [Genesis, ..., tip]"""
        chain = []
        curr = tip_hash
        while curr:
            chain.append(curr)
            curr = self.blocks[curr].selected_parent if curr in self.blocks else None
        chain.reverse()
        return chain

    def add_block(self, block: DAGBlock) -> bool:
        """
        Validate and insert a new block into the BlockDAG.
        Executes GHOSTDAG parent selection, blue score calculation,
        Virtual Selected Chain single-path issuance, and UTXO state application.
        """
        if block.hash in self.blocks:
            return False

        # Validate parents exist
        for parent_hash in block.parents:
            if parent_hash not in self.blocks:
                raise ValueError(f"Parent block {parent_hash} not found in DAG")

        # 1. Select Selected Parent (parent with highest blue work, tie-break by blue_score and hash)
        selected_parent_hash = max(
            block.parents,
            key=lambda p: (self.blocks[p].blue_work, self.blocks[p].blue_score, self.blocks[p].hash)
        )
        selected_parent = self.blocks[selected_parent_hash]
        block.selected_parent = selected_parent_hash

        # 2. Compute GHOSTDAG Merge Set
        merge_set = []
        for p in block.parents:
            if p not in merge_set and p != selected_parent_hash:
                merge_set.append(p)
        block.merge_set = merge_set

        # 3. Calculate Blue Score and Blue Work
        blue_add = 1 + sum(1 for m in merge_set if self.blocks[m].is_blue)
        block.blue_score = selected_parent.blue_score + blue_add
        block.blue_work = selected_parent.blue_work + (1 << max(1, block.bits))

        # Re-compute hash with confirmed blue score
        block.hash = block.compute_hash()

        # Insert block into blocks map
        self.blocks[block.hash] = block

        # 4. Check if this block advances or reorganizes the Selected Tip
        selected_block = self.blocks[self.selected_tip]
        # In GHOSTDAG, only blocks with strictly higher blue work reorganize the tip
        is_new_selected_tip = (block.blue_work > selected_block.blue_work)

        if is_new_selected_tip:
            old_chain = list(self.virtual_selected_parent_chain)
            new_chain = self.get_selected_parent_chain(block.hash)

            # Check common ancestor
            common_ancestor_idx = 0
            while common_ancestor_idx < len(old_chain) and common_ancestor_idx < len(new_chain):
                if old_chain[common_ancestor_idx] == new_chain[common_ancestor_idx]:
                    common_ancestor_idx += 1
                else:
                    break

            # If reorg occurred, revert orphaned blocks in reverse order
            for orphaned_hash in reversed(old_chain[common_ancestor_idx:]):
                orphaned_blk = self.blocks[orphaned_hash]
                self.utxo_set.revert_block(orphaned_hash)
                # Deduct un-minted subsidy exactly
                self.issued_supply_sats -= orphaned_blk.minted_sats
                self.pool_balance_sats -= orphaned_blk.pool_deposit_sats
                orphaned_blk.minted_sats = 0
                orphaned_blk.reward_sats = 0
                orphaned_blk.miner_base_sats = 0
            if common_ancestor_idx > 0:
                self.combo_machine.revert_to_height(common_ancestor_idx - 1)

            # Mint subsidies strictly for new selected blocks along the canonical sequence
            for idx in range(common_ancestor_idx, len(new_chain)):
                b_hash = new_chain[idx]
                b = self.blocks[b_hash]
                b_hash_bytes = bytes.fromhex(b.hash)
                
                # Single-chain sequential issuance: block_height = idx
                issuance: BlockIssuanceResult = compute_block_issuance(
                    block_height=idx,
                    block_hash=b_hash_bytes,
                    current_issued_sats=self.issued_supply_sats,
                    current_pool_balance_sats=self.pool_balance_sats
                )

                # Process Combo state machine for future beacon & winner resolution
                resolution, pool_deduction = self.combo_machine.process_block(
                    block_height=idx,
                    block_hash=b.hash,
                    current_pool_balance_satk=self.pool_balance_sats,
                    pool_deposit_satk=issuance.pool_deposit_sats
                )

                vesting_outputs = []
                if resolution:
                    winner_blk = self.blocks.get(resolution.winner_block_hash)
                    winner_cb_pub = winner_blk.coinbase_output[1].hex() if winner_blk else b.coinbase_output[1].hex()
                    winner_cb_ephem = winner_blk.coinbase_output[0].hex() if winner_blk else b.coinbase_output[0].hex()
                    
                    for tr in resolution.tranches:
                        vesting_outputs.append({
                            "tranche_index": tr["tranche_index"],
                            "amount_satk": tr["amount_satk"],
                            "unlock_block_height": tr["unlock_block_height"],
                            "P_onetime_hex": winner_cb_pub,
                            "R_ephem_hex": winner_cb_ephem
                        })
                    self.pool_balance_sats = issuance.new_pool_balance_sats - pool_deduction
                else:
                    self.pool_balance_sats = issuance.new_pool_balance_sats

                self.issued_supply_sats = issuance.new_issued_supply_sats
                b.is_combo = (resolution is not None)
                b.minted_sats = issuance.minted_sats
                b.reward_sats = issuance.miner_base_sats
                b.miner_base_sats = issuance.miner_base_sats
                b.pool_deposit_sats = issuance.pool_deposit_sats
                b.combo_payout_sats = pool_deduction if resolution else 0

                # Apply block to UTXO set
                applied, err = self.utxo_set.apply_block(
                    block_hash=b.hash,
                    block_height=idx,
                    coinbase_output=b.coinbase_output,
                    reward_sats=b.reward_sats,
                    transactions=b.transactions,
                    vesting_outputs=vesting_outputs
                )
                if not applied:
                    raise ValueError(f"Consensus failure applying block {b.hash}: {err}")

            self.selected_tip = block.hash
            self.virtual_selected_parent_chain = new_chain

        else:
            # Parallel or non-selected block:
            # DO NOT mint subsidy! Parallel blocks receive 0 subsidy until merged into selected chain
            block.is_combo = False
            block.minted_sats = 0
            block.reward_sats = 0
            block.miner_base_sats = 0
            block.pool_deposit_sats = 0
            block.combo_payout_sats = 0

            # Still apply transactions if valid without coinbase
            applied, err = self.utxo_set.apply_block(
                block_hash=block.hash,
                block_height=block.blue_score,
                coinbase_output=block.coinbase_output,
                reward_sats=0,
                transactions=block.transactions
            )
            if not applied:
                raise ValueError(f"Parallel block rejected by UTXO validation: {err}")

        # Update DAG tips: remove parents from tips, add new block
        for p in block.parents:
            self.tips.discard(p)
        self.tips.add(block.hash)

        return True

    def get_tips(self) -> List[str]:
        return list(self.tips)

    def get_dag_info(self) -> dict:
        selected = self.blocks[self.selected_tip]
        return {
            "genesis_hash": self.genesis_hash,
            "selected_tip": self.selected_tip,
            "max_blue_score": selected.blue_score,
            "virtual_selected_chain_length": len(self.virtual_selected_parent_chain),
            "total_blocks": len(self.blocks),
            "tips_count": len(self.tips),
            "tips": list(self.tips),
            "issued_supply_sats": self.issued_supply_sats,
            "issued_supply_btck": self.issued_supply_sats / SATS_PER_COIN,
            "pool_balance_sats": self.pool_balance_sats,
            "pool_balance_btck": self.pool_balance_sats / SATS_PER_COIN
        }
