"""
BTC-K High-Speed BlockDAG Engine (GHOSTDAG Consensus & Virtual Chain Issuance)
-----------------------------------------------------------------------------
1.0 Second BlockDAG architecture:
1. Multi-parent DAG topologies with GHOSTDAG linear ordering.
2. Deterministic virtual blue-block order for issuance (P0-01):
   Blue blocks receive sequential rewards; red/unmerged blocks receive none.
3. Canonical Block Header serialization aligned with Genesis (P0-04).
4. Full integration with Minimal Consensus UTXO Set (P0-06).
"""

import hashlib
import copy
import json
import time
from collections import OrderedDict
from dataclasses import asdict
from typing import List, Dict, Set, Optional, Tuple
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from consensus.genesis import calculate_block_pow, generate_genesis_block
from tokenomics.emission_schedule import (
    get_base_block_reward_sats,
    is_combo_block,
    SATS_PER_COIN,
    compute_block_issuance,
    BlockIssuanceResult,
    TOTAL_HARD_CAP_SATK,
    get_combo_spec_for_height,
)
from node.utxo_set import UTXOSet
from privacy.wallet_engine import StealthTransaction
from consensus.combo_state_machine import ComboStateMachine
from config.network import NetworkConfig, load_network
from consensus.pow import block_work, hash_meets_target
from consensus.daa import next_difficulty_bits


def transaction_commitment(transactions: List[StealthTransaction]) -> bytes:
    """Commit to transaction count and ordered transaction IDs."""
    payload = len(transactions).to_bytes(4, "little")
    for tx in transactions:
        try:
            txid = bytes.fromhex(tx.txid)
        except (TypeError, ValueError) as exc:
            raise ValueError("Transaction ID must be hex") from exc
        if len(txid) != 32:
            raise ValueError("Transaction ID must be 32 bytes")
        payload += txid
    return hashlib.sha256(payload).digest()

class DAGBlock:
    def __init__(
        self,
        parents: List[str],
        timestamp: float,
        bits: int,
        nonce: int,
        coinbase_output: Tuple[bytes, bytes], # (R_ephem, P_onetime)
        transactions: Optional[List[StealthTransaction]] = None,
        version: Optional[int] = None,
        blue_score: int = 0,
        payload_hash: bytes = b"",
        chain_id: Optional[str] = None,
        _known_hash: Optional[str] = None,
    ):
        default_network = load_network() if version is None or chain_id is None else None
        self.version = version if version is not None else default_network.block_version
        self.chain_id = chain_id if chain_id is not None else default_network.chain_id
        self.parents = parents # List of parent block hashes
        self.timestamp = timestamp
        self.bits = bits
        self.nonce = nonce
        self.blue_score = blue_score
        self.coinbase_output = coinbase_output
        self.transactions: List[StealthTransaction] = transactions or []
        self.payload_hash = payload_hash or transaction_commitment(self.transactions)
        
        # GHOSTDAG topological fields
        self.blue_work: int = 0
        self.selected_parent: Optional[str] = None
        self.merge_set: List[str] = []
        self.merge_set_blues: List[str] = []
        self.merge_set_reds: List[str] = []
        self.blue_anticone_sizes: Dict[str, int] = {}
        self.is_combo: bool = False
        self.minted_sats: int = 0
        self.reward_sats: int = 0
        self.miner_base_sats: int = 0
        self.pool_deposit_sats: int = 0
        self.eco_deposit_sats: int = 0
        self.combo_payout_sats: int = 0
        self.fees_sats: int = 0
        
        # Durable replay may supply the already-validated hash for history
        # covered by a state-root checkpoint. Live/network blocks never do.
        self.hash = _known_hash if _known_hash is not None else self.compute_hash()

    def compute_hash(self) -> str:
        return calculate_block_pow(
            version=self.version,
            chain_id=self.chain_id,
            parents=self.parents,
            timestamp=self.timestamp,
            bits=self.bits,
            nonce=self.nonce,
            coinbase_output=self.coinbase_output,
            payload_hash=self.payload_hash,
        ).hex()

class BlockDAG:
    """
    Persistent GHOSTDAG graph maintaining DAG tips,
    virtual blue order, capped sequential issuance, and UTXO consensus state.
    """
    def __init__(self, k_cluster: Optional[int] = None, network: Optional[NetworkConfig | str] = None):
        self.network = network if isinstance(network, NetworkConfig) else load_network(network)
        if k_cluster is not None and k_cluster != self.network.ghostdag_k:
            raise ValueError("GHOSTDAG K is consensus-bound by the network manifest")
        self.k_cluster = self.network.ghostdag_k
        self.blocks: Dict[str, DAGBlock] = {}
        self.tips: Set[str] = set()
        self.selected_tip: Optional[str] = None
        self.genesis_hash: str = ""
        self.issued_supply_sats: int = 0
        self.pool_balance_sats: int = 0
        self.virtual_selected_parent_chain: List[str] = []
        self.virtual_blue_order: List[str] = []
        self.utxo_set = UTXOSet()
        self.combo_machine = ComboStateMachine()
        # DAG ancestry is immutable once a block is admitted.  GHOSTDAG merge
        # classification asks the same reachability questions many times, so a
        # bounded cache removes repeated full-past traversals without changing
        # any consensus decision.
        self._ancestor_cache: OrderedDict[Tuple[str, str], bool] = OrderedDict()
        self._ancestor_cache_limit = max(
            10_000,
            int(os.getenv("BTCK_ANCESTOR_CACHE_SIZE", "262144")),
        )
        
        self._init_genesis()

    def _init_genesis(self):
        """Construct the Immutable Genesis Block #0 with cypherpunk headline"""
        g = generate_genesis_block(self.network)
        
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
            payload_hash=headline_hash,
            chain_id=self.network.chain_id,
        )
        genesis.blue_score = 0
        genesis.blue_work = 0
        genesis.reward_sats = g["reward_sats"]
        genesis.minted_sats = 0
        
        if genesis.hash != g["hash"] or genesis.hash != self.network.genesis.expected_hash:
            raise ValueError(
                f"{self.network.name} genesis hash mismatch: {genesis.hash} != "
                f"{self.network.genesis.expected_hash}"
            )
        if not hash_meets_target(bytes.fromhex(genesis.hash), genesis.bits, self.network.pow_limit_bits):
            raise ValueError(f"{self.network.name} genesis does not satisfy its proof-of-work target")
        
        self.blocks[genesis.hash] = genesis
        self.tips.add(genesis.hash)
        self.selected_tip = genesis.hash
        self.genesis_hash = genesis.hash
        self.virtual_selected_parent_chain = [genesis.hash]
        self.virtual_blue_order = [genesis.hash]

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

    def get_mining_parents(self) -> List[str]:
        """Return a deterministic, consensus-bounded parent set for templates.

        The selected tip must remain the first parent.  The remaining slots are
        deliberately used for the lowest-work, oldest tips.  ASICs can return
        valid work for an earlier template several seconds late; without this
        cleanup policy those side tips lose every descending-work tie-break and
        accumulate forever.
        """
        selected = self.selected_tip
        if selected is None:
            return []
        candidates = sorted(
            (tip for tip in self.tips if tip != selected),
            key=lambda tip: (
                self.blocks[tip].blue_work,
                self.blocks[tip].timestamp,
                tip,
            ),
        )
        return [selected, *candidates[: self.network.max_parents - 1]]

    def expected_bits(self, parents: List[str]) -> int:
        if not parents:
            raise ValueError("At least one parent is required for DAA")
        for parent in parents:
            if parent not in self.blocks:
                raise ValueError(f"Parent block {parent} not found in DAG")
        selected_parent = max(
            parents,
            key=lambda parent: (self.blocks[parent].blue_work, parent),
        )
        # DAA consumes only its bounded trailing window. Rebuilding the full
        # selected-parent chain for every candidate made admission O(height)
        # and caused measurable throughput decay during the 24-hour soak.
        recent = []
        current = selected_parent
        while current != self.genesis_hash and len(recent) < self.network.daa_window_blocks:
            recent.append(self.blocks[current])
            current = self.blocks[current].selected_parent
        chain = [self.blocks[self.genesis_hash], *reversed(recent)]
        return next_difficulty_bits(chain, self.network)

    def get_virtual_blue_order(self, tip_hash: str) -> List[str]:
        """Return the deterministic blue-block order for ledger processing."""
        selected_chain = self.get_selected_parent_chain(tip_hash)
        order: List[str] = []
        seen: Set[str] = set()
        for block_hash in selected_chain:
            block = self.blocks[block_hash]
            # The selected parent is already present. Remaining entries follow
            # canonical merge-set work/hash order and precede the merging block.
            for blue_hash in block.merge_set_blues[1:]:
                if blue_hash not in seen:
                    order.append(blue_hash)
                    seen.add(blue_hash)
            if block_hash not in seen:
                order.append(block_hash)
                seen.add(block_hash)
        return order

    def _is_dag_ancestor(self, ancestor_hash: str, descendant_hash: str) -> bool:
        """Return whether ancestor_hash is in descendant_hash's inclusive DAG past."""
        cache_key = (ancestor_hash, descendant_hash)
        cached = self._ancestor_cache.get(cache_key)
        if cached is not None:
            self._ancestor_cache.move_to_end(cache_key)
            return cached
        if ancestor_hash == descendant_hash:
            self._cache_ancestor_result(cache_key, True)
            return True
        ancestor_work = self.blocks[ancestor_hash].blue_work
        pending = list(self.blocks[descendant_hash].parents)
        visited: Set[str] = set()
        while pending:
            current = pending.pop()
            if current == ancestor_hash:
                self._cache_ancestor_result(cache_key, True)
                return True
            if current in visited:
                continue
            visited.add(current)
            # Blue work is strictly increasing across every accepted parent
            # edge. Once a branch is at or below the candidate ancestor's work
            # and is not that exact block, none of its older parents can be the
            # candidate. This turns the common sibling-tip negative query from
            # a full-history walk into a bounded near-tip check.
            if self.blocks[current].blue_work <= ancestor_work:
                continue
            pending.extend(self.blocks[current].parents)
        self._cache_ancestor_result(cache_key, False)
        return False

    def _cache_ancestor_result(self, key: Tuple[str, str], result: bool) -> None:
        self._ancestor_cache[key] = result
        self._ancestor_cache.move_to_end(key)
        while len(self._ancestor_cache) > self._ancestor_cache_limit:
            self._ancestor_cache.popitem(last=False)

    def _calculate_merge_set(self, selected_parent_hash: str, parents: List[str]) -> List[str]:
        """Calculate past(parents) minus past(selected parent), then order deterministically."""
        merge_set: Set[str] = set()
        pending = [parent for parent in parents if parent != selected_parent_hash]
        while pending:
            current = pending.pop()
            if current in merge_set or self._is_dag_ancestor(current, selected_parent_hash):
                continue
            merge_set.add(current)
            if len(merge_set) > self.network.max_merge_set_size:
                raise ValueError("Block merge set exceeds the network consensus limit")
            pending.extend(self.blocks[current].parents)
        return sorted(merge_set, key=lambda h: (self.blocks[h].blue_work, h))

    def _blue_anticone_size(
        self, blue_hash: str, selected_parent_hash: str, current_sizes: Dict[str, int]
    ) -> int:
        """Find the newest stored anticone size for a blue in this context."""
        if blue_hash in current_sizes:
            return current_sizes[blue_hash]
        current: Optional[str] = selected_parent_hash
        while current is not None:
            block = self.blocks[current]
            if blue_hash in block.blue_anticone_sizes:
                return block.blue_anticone_sizes[blue_hash]
            current = block.selected_parent
        raise ValueError("Corrupt GHOSTDAG state: missing blue anticone size")

    def _classify_merge_set(
        self, selected_parent_hash: str, ordered_merge_set: List[str]
    ) -> Tuple[List[str], List[str], Dict[str, int]]:
        """Greedily construct the K-cluster using bounded context metadata."""
        merge_blues = [selected_parent_hash]
        merge_reds: List[str] = []
        anticone_sizes: Dict[str, int] = {selected_parent_hash: 0}

        for candidate in ordered_merge_set:
            if len(merge_blues) == self.k_cluster + 1:
                merge_reds.append(candidate)
                continue

            candidate_anticone_size = 0
            affected: Dict[str, int] = {}
            context_blues = list(merge_blues)
            current: Optional[str] = None
            is_blue = True

            while True:
                if current is not None and self._is_dag_ancestor(current, candidate):
                    break
                for peer in context_blues:
                    if peer == candidate or peer in affected or self._is_dag_ancestor(peer, candidate):
                        continue
                    peer_size = self._blue_anticone_size(
                        peer, selected_parent_hash, anticone_sizes
                    )
                    candidate_anticone_size += 1
                    if candidate_anticone_size > self.k_cluster or peer_size >= self.k_cluster:
                        is_blue = False
                        break
                    affected[peer] = peer_size
                if not is_blue or current == self.genesis_hash:
                    break
                current = selected_parent_hash if current is None else self.blocks[current].selected_parent
                context_blues = self.blocks[current].merge_set_blues if current is not None else []

            if is_blue:
                merge_blues.append(candidate)
                anticone_sizes[candidate] = candidate_anticone_size
                for peer, old_size in affected.items():
                    anticone_sizes[peer] = old_size + 1
            else:
                merge_reds.append(candidate)
        return merge_blues, merge_reds, anticone_sizes

    def _validate_block(self, block: DAGBlock, *, verify_header_hash: bool = True) -> None:
        """Validate every stateless/header rule without mutating consensus state."""
        if block.hash in self.blocks:
            raise ValueError("Block already exists")
        if block.chain_id != self.network.chain_id:
            raise ValueError("Block chain_id does not match this node's network")
        if block.version != self.network.block_version:
            raise ValueError(f"Unsupported block version: {block.version}")
        if not block.parents:
            raise ValueError("Non-genesis block must have at least one parent")
        if len(block.parents) > self.network.max_parents:
            raise ValueError("Block exceeds the network parent limit")
        if len(set(block.parents)) != len(block.parents):
            raise ValueError("Block contains duplicate parents")
        for parent_hash in block.parents:
            if parent_hash not in self.blocks:
                raise ValueError(f"Parent block {parent_hash} not found in DAG")
        expected_bits = self.expected_bits(block.parents)
        if block.bits != expected_bits:
            raise ValueError(
                f"Block target bits do not match DAA: {block.bits:#x} != {expected_bits:#x}"
            )
        if len(block.coinbase_output) != 2 or any(
            len(point) != 33 or point[0] not in (2, 3) for point in block.coinbase_output
        ):
            raise ValueError("Coinbase output must contain two compressed secp256k1 points")
        if len(block.payload_hash) != 32:
            raise ValueError("Block payload commitment must be 32 bytes")
        if block.payload_hash != transaction_commitment(block.transactions):
            raise ValueError("Block payload commitment does not match its transactions")
        if verify_header_hash and block.compute_hash() != block.hash:
            raise ValueError("Block header was mutated after hashing")

        newest_parent_time = max(self.blocks[parent].timestamp for parent in block.parents)
        if block.timestamp <= newest_parent_time:
            raise ValueError("Block timestamp must be greater than every parent timestamp")
        if block.timestamp > time.time() + self.network.max_future_seconds:
            raise ValueError("Block timestamp is too far in the future")
        if not hash_meets_target(bytes.fromhex(block.hash), block.bits, self.network.pow_limit_bits):
            raise ValueError("Block proof of work does not meet target")

    def add_block(self, block: DAGBlock, commit_hook=None) -> bool:
        """Validate and atomically apply a block to all in-memory consensus state."""
        if block.hash in self.blocks:
            return False
        self._validate_block(block)

        # Most ASIC submissions on a live DAG are parallel/side blocks.  They
        # cannot mutate the selected ledger, UTXO set, issuance, or Combo state,
        # so copying the entire accumulated DAG before admitting every one of
        # them makes block admission O(chain length).  Populate the candidate's
        # deterministic GHOSTDAG metadata first and use a small rollback scope
        # when it cannot become the selected tip.  Selected-tip changes retain
        # the full transactional snapshot below.
        self._populate_ghostdag_metadata(block)
        selected_block = self.blocks[self.selected_tip]
        is_new_selected_tip = (block.blue_work, block.hash) > (
            selected_block.blue_work,
            selected_block.hash,
        )
        if not is_new_selected_tip:
            old_tips = set(self.tips)
            self.blocks[block.hash] = block
            block.is_combo = False
            block.minted_sats = 0
            block.reward_sats = 0
            block.miner_base_sats = 0
            block.pool_deposit_sats = 0
            block.eco_deposit_sats = 0
            block.combo_payout_sats = 0
            block.fees_sats = 0
            for parent in block.parents:
                self.tips.discard(parent)
            self.tips.add(block.hash)
            try:
                if commit_hook is not None:
                    commit_hook(block)
            except Exception:
                self.blocks.pop(block.hash, None)
                self.tips = old_tips
                raise
            return True

        if block.selected_parent == self.selected_tip:
            # A normal selected-tip extension cannot rewrite historical
            # GHOSTDAG topology.  It may account a bounded set of merge blues,
            # so snapshot only their mutable issuance fields plus the ledger
            # views that the extension changes.  This keeps the common live
            # admission path independent of total chain length while retaining
            # atomic rollback if UTXO validation or durable persistence fails.
            accounting_fields = (
                "is_combo",
                "minted_sats",
                "reward_sats",
                "miner_base_sats",
                "pool_deposit_sats",
                "eco_deposit_sats",
                "combo_payout_sats",
                "fees_sats",
            )
            affected_hashes = [
                blue_hash
                for blue_hash in block.merge_set_blues[1:]
                if blue_hash in self.blocks
            ]
            affected_accounting = {
                blue_hash: tuple(
                    getattr(self.blocks[blue_hash], field)
                    for field in accounting_fields
                )
                for blue_hash in affected_hashes
            }
            applied_hashes = [*affected_hashes, block.hash]
            old_selected_chain_length = len(self.virtual_selected_parent_chain)
            old_blue_order_length = len(self.virtual_blue_order)
            snapshot = (
                set(self.tips),
                self.selected_tip,
                self.issued_supply_sats,
                self.pool_balance_sats,
            )
            try:
                accepted = self._commit_validated_block(block, metadata_ready=True)
                if accepted and commit_hook is not None:
                    commit_hook(block)
                return accepted
            except Exception:
                # UTXOSet already records exact per-block created/spent undo.
                # Roll back only applications performed by this extension,
                # newest first; do not copy the lifetime undo journal.
                for applied_hash in reversed(applied_hashes):
                    if (
                        applied_hash in self.utxo_set._block_created_undo
                        or applied_hash in self.utxo_set._block_spent_undo
                    ):
                        self.utxo_set.revert_block(applied_hash)
                self.blocks.pop(block.hash, None)
                (
                    self.tips,
                    self.selected_tip,
                    self.issued_supply_sats,
                    self.pool_balance_sats,
                ) = snapshot
                del self.virtual_selected_parent_chain[old_selected_chain_length:]
                del self.virtual_blue_order[old_blue_order_length:]
                self.combo_machine.revert_to_height(old_blue_order_length - 1)
                for blue_hash, values in affected_accounting.items():
                    blue = self.blocks[blue_hash]
                    for field, value in zip(accounting_fields, values):
                        setattr(blue, field, value)
                raise

        # A selected-tip reorganization can touch historical issuance fields,
        # but it never rewrites stored header/GHOSTDAG topology.  Preserve the
        # small mutable surface explicitly and shallow-copy UTXO maps (entries
        # and undo records are replaced, not mutated).  Deep-copying every DAG
        # block here made stale-parent ASIC submissions block RPC for seconds.
        accounting_fields = (
            "is_combo",
            "minted_sats",
            "reward_sats",
            "miner_base_sats",
            "pool_deposit_sats",
            "eco_deposit_sats",
            "combo_payout_sats",
            "fees_sats",
        )
        accounting_snapshot = {
            block_hash: tuple(getattr(stored, field) for field in accounting_fields)
            for block_hash, stored in self.blocks.items()
        }
        utxo_snapshot = (
            dict(self.utxo_set.utxos),
            dict(self.utxo_set._block_spent_undo),
            dict(self.utxo_set._block_created_undo),
            dict(self.utxo_set._block_fees),
            dict(self.utxo_set._block_accepted_txids),
        )
        snapshot = (
            set(self.tips),
            self.selected_tip,
            self.issued_supply_sats,
            self.pool_balance_sats,
            list(self.virtual_selected_parent_chain),
            list(self.virtual_blue_order),
            copy.deepcopy(self.combo_machine),
        )
        try:
            accepted = self._commit_validated_block(block, metadata_ready=True)
            if accepted and commit_hook is not None:
                commit_hook(block)
            return accepted
        except Exception:
            self.blocks.pop(block.hash, None)
            (
                self.tips,
                self.selected_tip,
                self.issued_supply_sats,
                self.pool_balance_sats,
                self.virtual_selected_parent_chain,
                self.virtual_blue_order,
                self.combo_machine,
            ) = snapshot
            (
                self.utxo_set.utxos,
                self.utxo_set._block_spent_undo,
                self.utxo_set._block_created_undo,
                self.utxo_set._block_fees,
                self.utxo_set._block_accepted_txids,
            ) = utxo_snapshot
            for block_hash, values in accounting_snapshot.items():
                stored = self.blocks[block_hash]
                for field, value in zip(accounting_fields, values):
                    setattr(stored, field, value)
            raise

    def replay_block(self, block: DAGBlock) -> bool:
        """Validate and apply one persisted block without an O(n) rollback copy.

        This is only for startup replay into a newly constructed DAG.  A replay
        failure aborts node initialization, so copying the entire accumulated
        chain before every record provides no recovery benefit and makes startup
        quadratic.  Live block admission must continue to use ``add_block``.
        """
        if block.hash in self.blocks:
            return False
        # ChainStore.deserialize_block already recomputed and matched the
        # canonical header hash. Avoid performing the expensive kHeavyHash a
        # second time for the same durable record during startup.
        self._validate_block(block, verify_header_hash=False)
        return self._commit_validated_block(block)

    def _commit_validated_block(
        self, block: DAGBlock, *, metadata_ready: bool = False
    ) -> bool:
        """Mutating half of add_block; caller provides rollback protection."""

        if not metadata_ready:
            self._populate_ghostdag_metadata(block)

        selected_parent = self.blocks[block.selected_parent]

        # Insert block into blocks map
        self.blocks[block.hash] = block

        # 4. Check if this block advances or reorganizes the Selected Tip
        selected_block = self.blocks[self.selected_tip]
        # Work/hash tie-breaking must be arrival-order independent across nodes.
        is_new_selected_tip = (
            block.blue_work, block.hash
        ) > (
            selected_block.blue_work, selected_block.hash
        )

        if is_new_selected_tip:
            is_linear_extension = block.selected_parent == self.selected_tip
            if is_linear_extension:
                # Do not copy either lifetime chain here.  Python list
                # concatenation is O(chain length), so doing it for every
                # ordinary extension makes both live admission and startup
                # replay quadratic.  Only the bounded new suffix is needed
                # while applying issuance; append it after validation.
                old_chain = self.virtual_blue_order
                chain_extension = block.merge_set_blues[1:] + [block.hash]
                common_ancestor_idx = len(old_chain)
                blocks_to_apply = enumerate(chain_extension, start=common_ancestor_idx)
                # Newly merged blues are not members of the selected-parent
                # chain.  Of this suffix, only the extending block is selected.
                selected_chain_set = {block.hash}
            else:
                new_selected_chain = self.get_selected_parent_chain(block.hash)
                old_chain = self.virtual_blue_order
                new_chain = self.get_virtual_blue_order(block.hash)
                common_ancestor_idx = 0
                while common_ancestor_idx < len(old_chain) and common_ancestor_idx < len(new_chain):
                    if old_chain[common_ancestor_idx] == new_chain[common_ancestor_idx]:
                        common_ancestor_idx += 1
                    else:
                        break
                blocks_to_apply = enumerate(
                    new_chain[common_ancestor_idx:], start=common_ancestor_idx
                )
                selected_chain_set = set(new_selected_chain)

            # Hard checkpoints are indexed by deterministic virtual blue-order
            # height, not raw DAG arrival order. Reject a candidate selected
            # view before reverting or minting anything if it would replace a
            # manifest-pinned historical hash.
            prospective_suffix = (
                chain_extension
                if is_linear_extension
                else new_chain[common_ancestor_idx:]
            )
            for checkpoint_height, expected_hash in self.network.hard_checkpoints.items():
                if checkpoint_height < common_ancestor_idx:
                    # The already-accepted prefix was checked when it entered
                    # the virtual order; any reorg below it would appear in the
                    # suffix and be checked below.
                    continue
                suffix_index = checkpoint_height - common_ancestor_idx
                if 0 <= suffix_index < len(prospective_suffix):
                    actual_hash = prospective_suffix[suffix_index]
                    if actual_hash != expected_hash:
                        raise ValueError(
                            "Hard checkpoint mismatch at virtual blue height "
                            f"{checkpoint_height}: {actual_hash} != {expected_hash}"
                        )

            # If reorg occurred, revert orphaned blocks in reverse order
            for orphaned_hash in reversed(old_chain[common_ancestor_idx:]):
                orphaned_blk = self.blocks[orphaned_hash]
                self.utxo_set.revert_block(orphaned_hash)
                # Deduct un-minted subsidy exactly
                self.issued_supply_sats -= orphaned_blk.minted_sats
                self.pool_balance_sats -= orphaned_blk.pool_deposit_sats
                self.pool_balance_sats += orphaned_blk.combo_payout_sats
                orphaned_blk.minted_sats = 0
                orphaned_blk.reward_sats = 0
                orphaned_blk.miner_base_sats = 0
                orphaned_blk.pool_deposit_sats = 0
                orphaned_blk.eco_deposit_sats = 0
                orphaned_blk.combo_payout_sats = 0
                orphaned_blk.fees_sats = 0
            if common_ancestor_idx > 0:
                self.combo_machine.revert_to_height(common_ancestor_idx - 1)

            # Mint subsidies for blue blocks along the canonical virtual order.
            for idx, b_hash in blocks_to_apply:
                b = self.blocks[b_hash]
                b_hash_bytes = bytes.fromhex(b.hash)
                allow_transaction_skips = b_hash not in selected_chain_set
                previewed_fees = self.utxo_set.preview_block_fees(
                    b.transactions,
                    idx,
                    allow_transaction_skips=allow_transaction_skips,
                )
                
                # Sequential blue height preserves the fixed emission cap.
                issuance: BlockIssuanceResult = compute_block_issuance(
                    block_height=idx,
                    block_hash=b_hash_bytes,
                    current_issued_sats=self.issued_supply_sats,
                    current_pool_balance_sats=self.pool_balance_sats,
                    total_fee_sats=previewed_fees,
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
                b.eco_deposit_sats = issuance.eco_deposit_sats
                b.combo_payout_sats = pool_deduction if resolution else 0

                # Apply block to UTXO set
                applied, err = self.utxo_set.apply_block(
                    block_hash=b.hash,
                    block_height=idx,
                    coinbase_output=b.coinbase_output,
                    reward_sats=b.reward_sats,
                    transactions=b.transactions,
                    vesting_outputs=vesting_outputs,
                    eco_output=(
                        issuance.eco_deposit_sats,
                        self.network.eco_fund_output[0],
                        self.network.eco_fund_output[1],
                    ),
                    pool_deposit_sats=issuance.pool_deposit_sats,
                    pool_payout_sats=pool_deduction,
                    expected_miner_fee_sats=issuance.miner_fee_sats,
                    fee_pool_deposit_sats=issuance.combo_fee_deposit_sats,
                    allow_transaction_skips=allow_transaction_skips,
                )
                if not applied:
                    raise ValueError(f"Consensus failure applying block {b.hash}: {err}")
                b.fees_sats = self.utxo_set.get_block_fees(b.hash)

                if self.utxo_set.total_value_sats() != self.issued_supply_sats:
                    raise ValueError("Consensus asset conservation failure: UTXO total != issued supply")
                if self.utxo_set.value_by_kind_sats("jackpot_pool") != self.pool_balance_sats:
                    raise ValueError("Consensus jackpot balance does not match pool UTXOs")

            self.selected_tip = block.hash
            if is_linear_extension:
                self.virtual_selected_parent_chain.append(block.hash)
                self.virtual_blue_order.extend(chain_extension)
            else:
                self.virtual_selected_parent_chain = new_selected_chain
                self.virtual_blue_order = new_chain

        else:
            # Unmerged or red block: no subsidy until a future blue merge.
            block.is_combo = False
            block.minted_sats = 0
            block.reward_sats = 0
            block.miner_base_sats = 0
            block.pool_deposit_sats = 0
            block.eco_deposit_sats = 0
            block.combo_payout_sats = 0
            block.fees_sats = 0

            # Side-branch transactions are stored with the block but never touch
            # the selected-chain UTXO view. They are validated/applied only if a
            # later block makes this branch canonical.

        # Update DAG tips: remove parents from tips, add new block
        for p in block.parents:
            self.tips.discard(p)
        self.tips.add(block.hash)
        return True

    def _populate_ghostdag_metadata(self, block: DAGBlock) -> None:
        """Populate deterministic GHOSTDAG fields without mutating DAG state."""

        # 1. Select Selected Parent using the GHOSTDAG work/hash ordering.
        selected_parent_hash = max(
            block.parents,
            key=lambda p: (self.blocks[p].blue_work, self.blocks[p].hash)
        )
        selected_parent = self.blocks[selected_parent_hash]
        block.selected_parent = selected_parent_hash

        # 2. Compute the complete merge set and classify it into the K-cluster.
        block.merge_set = self._calculate_merge_set(selected_parent_hash, block.parents)
        (
            block.merge_set_blues,
            block.merge_set_reds,
            block.blue_anticone_sizes,
        ) = self._classify_merge_set(selected_parent_hash, block.merge_set)

        # 3. Blue score/work include the selected parent and all accepted merge blues.
        block.blue_score = selected_parent.blue_score + len(block.merge_set_blues)
        block.blue_work = (
            selected_parent.blue_work
            + block_work(block.bits, self.network.pow_limit_bits)
            + sum(
                block_work(self.blocks[blue].bits, self.network.pow_limit_bits)
                for blue in block.merge_set_blues[1:]
            )
        )

    def get_tips(self) -> List[str]:
        if len(self.tips) <= self.network.max_parents:
            return list(self.tips)
        # Keep generic block construction aligned with the mining-template tip
        # cleanup policy.  The selected tip anchors consensus; remaining parent
        # capacity drains stranded low-work/old tips deterministically.
        other_tips = [t for t in self.tips if t != self.selected_tip]
        sorted_others = sorted(
            other_tips,
            key=lambda t: (
                self.blocks[t].blue_work,
                self.blocks[t].timestamp,
                self.blocks[t].hash,
            ),
        )
        selected = [self.selected_tip] if self.selected_tip in self.tips else []
        remaining_slots = self.network.max_parents - len(selected)
        return selected + sorted_others[:remaining_slots]

    def state_root(self) -> str:
        """Deterministic commitment used to verify persisted replay results."""
        blocks = []
        for block_hash in sorted(self.blocks):
            block = self.blocks[block_hash]
            blocks.append({
                "hash": block_hash,
                "parents": sorted(block.parents),
                "selected_parent": block.selected_parent,
                "merge_set": sorted(block.merge_set),
                "merge_set_blues": block.merge_set_blues,
                "merge_set_reds": block.merge_set_reds,
                "blue_anticone_sizes": dict(sorted(block.blue_anticone_sizes.items())),
                "blue_score": block.blue_score,
                "blue_work": block.blue_work,
                "minted_sats": block.minted_sats,
                "miner_base_sats": block.miner_base_sats,
                "eco_deposit_sats": block.eco_deposit_sats,
                "pool_deposit_sats": block.pool_deposit_sats,
                "combo_payout_sats": block.combo_payout_sats,
                "fees_sats": block.fees_sats,
                "accepted_txids": self.utxo_set.get_block_accepted_txids(block_hash),
            })
        utxos = []
        for (txid, vout), entry in sorted(self.utxo_set.utxos.items()):
            utxos.append({"txid": txid, "vout": vout, **asdict(entry)})
        epochs = {
            str(epoch_id): asdict(epoch)
            for epoch_id, epoch in sorted(self.combo_machine.epochs.items())
        }
        payload = {
            "chain_id": self.network.chain_id,
            "blocks": blocks,
            "tips": sorted(self.tips),
            "selected_tip": self.selected_tip,
            "selected_chain": self.virtual_selected_parent_chain,
            "virtual_blue_order": self.virtual_blue_order,
            "issued_supply_sats": self.issued_supply_sats,
            "pool_balance_sats": self.pool_balance_sats,
            "utxos": utxos,
            "combo_epochs": epochs,
            "combo_pending": sorted(self.combo_machine.pending_draws),
            "combo_active": self.combo_machine.active_epoch_id,
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def get_dag_info(self, *, include_state_root: bool = True) -> dict:
        selected = self.blocks[self.selected_tip]
        next_bits = self.expected_bits(self.get_mining_parents())
        info = {
            "network": self.network.name,
            "chain_id": self.network.chain_id,
            "genesis_hash": self.genesis_hash,
            "selected_tip": self.selected_tip,
            "max_blue_score": selected.blue_score,
            "virtual_selected_chain_length": len(self.virtual_selected_parent_chain),
            "virtual_blue_order_length": len(self.virtual_blue_order),
            "total_blocks": len(self.blocks),
            "tips_count": len(self.tips),
            "tips": list(self.tips),
            "selected_bits": selected.bits,
            "next_bits": next_bits,
            "issued_supply_sats": self.issued_supply_sats,
            "issued_supply_btck": self.issued_supply_sats / SATS_PER_COIN,
            "pool_balance_sats": self.pool_balance_sats,
            "pool_balance_btck": self.pool_balance_sats / SATS_PER_COIN,
            "utxo_total_sats": self.utxo_set.total_value_sats(),
            "eco_fund_utxo_sats": self.utxo_set.value_by_kind_sats("eco"),
        }
        if include_state_root:
            info["state_root"] = self.state_root()
        return info

    def get_dashboard_info(self, recent_limit: int = 120) -> dict:
        """Return a lightweight, consensus-authoritative explorer snapshot.

        Unlike :meth:`get_dag_info`, this intentionally avoids rebuilding the
        full state root.  The explorer must never read the live SQLite/WAL
        files directly; all chain, issuance and Combo values come from this
        in-memory consensus view.
        """
        selected = self.blocks[self.selected_tip]
        order = self.virtual_blue_order
        ledger_height = max(0, len(order) - 1)
        limit = max(1, min(int(recent_limit), 50_000))
        first_index = max(0, len(order) - limit)
        recent_blocks = []
        for issuance_height, block_hash in enumerate(order[first_index:], start=first_index):
            block = self.blocks[block_hash]
            recent_blocks.append({
                "height": issuance_height,
                "hash": block_hash,
                "timestamp": block.timestamp,
                "blue_score": block.blue_score,
                "bits": block.bits,
                "miner_base_sats": block.miner_base_sats,
                "pool_deposit_sats": block.pool_deposit_sats,
                "eco_deposit_sats": block.eco_deposit_sats,
                "combo_payout_sats": block.combo_payout_sats,
                "fees_sats": block.fees_sats,
                "is_combo_resolution": bool(block.combo_payout_sats),
            })

        resolutions = []
        for epoch_id, epoch in sorted(self.combo_machine.epochs.items()):
            if not epoch.resolution:
                continue
            resolution = epoch.resolution
            start_block = self.blocks[order[epoch.start_height]]
            draw_block = self.blocks[order[resolution.draw_height]]
            resolutions.append({
                "epoch_id": epoch_id,
                "stage_id": resolution.stage_id,
                "epoch_start_height": epoch.start_height,
                "epoch_start_timestamp": start_block.timestamp,
                "draw_height": resolution.draw_height,
                "draw_timestamp": draw_block.timestamp,
                "beacon_height": resolution.beacon_height,
                "beacon_hash": resolution.beacon_hash,
                "winner_block_hash": resolution.winner_block_hash,
                "winner_distance_hex": f"{resolution.winner_distance:064x}",
                "jackpot_amount_satk": resolution.jackpot_amount_satk,
                "tranches": resolution.tranches,
            })

        active_epoch = self.combo_machine.epochs[self.combo_machine.active_epoch_id]
        _, _, draw_interval = get_combo_spec_for_height(max(1, active_epoch.start_height))
        next_draw_height = active_epoch.start_height + draw_interval - 1
        next_bits = self.expected_bits(self.get_mining_parents())
        return {
            "ledger_height": ledger_height,
            "max_blue_score": selected.blue_score,
            "total_dag_blocks": len(self.blocks),
            "selected_tip": self.selected_tip,
            "tips_count": len(self.tips),
            "selected_bits": selected.bits,
            "next_bits": next_bits,
            "issued_supply_sats": self.issued_supply_sats,
            "pool_balance_sats": self.pool_balance_sats,
            "jackpot_pool_utxo_sats": self.utxo_set.value_by_kind_sats("jackpot_pool"),
            "eco_fund_utxo_sats": self.utxo_set.value_by_kind_sats("eco"),
            "utxo_total_sats": self.utxo_set.total_value_sats(),
            "combo": {
                "active_epoch_id": self.combo_machine.active_epoch_id,
                "active_epoch_start_height": active_epoch.start_height,
                "next_draw_height": next_draw_height,
                "pending_epoch_ids": sorted(self.combo_machine.pending_draws),
                "resolved_count": len(resolutions),
                "resolutions": resolutions[-20:],
            },
            "recent_blocks": recent_blocks,
        }
