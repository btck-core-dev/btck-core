"""
BTC-K Minimal Consensus UTXO Set & Double-Spend Defense Engine (P0-06)
---------------------------------------------------------------------
Maintains the unspent transaction output (UTXO) state of the BTC-K BlockDAG:
1. Outpoint mapping: (txid, vout) -> UTXOEntry.
2. Strict double-spend prevention: inputs must exist in UTXO set and cannot be spent twice.
3. Cryptographic signature verification: spend authorization using Schnorr signatures.
4. Value conservation: Sum(Inputs) >= Sum(Outputs) + Fee.
5. Coinbase Maturity rule: Coinbase outputs require 120 block confirmations before spending.
6. Reversible state modifications to support BlockDAG reorganizations.
"""

from typing import Dict, List, Tuple, Optional, Set, Any
from dataclasses import dataclass, field
import hashlib
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from privacy.wallet_engine import (
    schnorr_verify,
    TxInput,
    TxOutput,
    StealthTransaction,
    sha256
)
from tokenomics.emission_schedule import get_regular_coinbase_maturity_blocks

COINBASE_MATURITY_CONFIRMATIONS = 7200

@dataclass(slots=True)
class UTXOEntry:
    amount_satk: int
    P_onetime_hex: str
    R_ephem_hex: str
    block_height: int
    is_coinbase: bool = False
    timelock_until_height: int = 0
    kind: str = "standard"


class TrackedUTXODict(dict):
    """Dictionary with O(1) value-conservation totals.

    Consensus code and tests intentionally expose the UTXO mapping. Keeping
    accounting in the mapping itself means direct fixture/import assignments
    cannot silently bypass the cached totals.
    """

    def __init__(self, values=()):
        super().__init__()
        self.total_satk = 0
        self.kind_totals: Dict[str, int] = {}
        self.update(values)

    def _add_entry(self, entry: UTXOEntry, sign: int) -> None:
        amount = int(entry.amount_satk) * sign
        self.total_satk += amount
        self.kind_totals[entry.kind] = self.kind_totals.get(entry.kind, 0) + amount
        if self.kind_totals[entry.kind] == 0:
            self.kind_totals.pop(entry.kind, None)

    def __setitem__(self, key, value) -> None:
        previous = self.get(key)
        if previous is not None:
            self._add_entry(previous, -1)
        super().__setitem__(key, value)
        self._add_entry(value, 1)

    def __delitem__(self, key) -> None:
        self._add_entry(self[key], -1)
        super().__delitem__(key)

    def pop(self, key, *default):
        if key not in self:
            if default:
                return default[0]
            raise KeyError(key)
        value = self[key]
        del self[key]
        return value

    def update(self, values=(), **kwargs) -> None:
        source = dict(values, **kwargs)
        for key, value in source.items():
            self[key] = value

    def clear(self) -> None:
        super().clear()
        self.total_satk = 0
        self.kind_totals.clear()

class UTXOSet:
    """
    Consensus UTXO Set tracking all spendable outputs across the BlockDAG.
    """
    def __init__(self):
        # Outpoint (txid, vout) -> UTXOEntry
        self._utxos = TrackedUTXODict()
        # Block hash -> list of (spent_outpoint, UTXOEntry) for rollbacks
        self._block_spent_undo: Dict[str, List[Tuple[Tuple[str, int], UTXOEntry]]] = {}
        # Block hash -> list of created outpoints for rollbacks
        self._block_created_undo: Dict[str, List[Tuple[str, int]]] = {}
        self._block_fees: Dict[str, int] = {}
        self._block_accepted_txids: Dict[str, List[str]] = {}

    @property
    def utxos(self) -> TrackedUTXODict:
        return self._utxos

    @utxos.setter
    def utxos(self, values) -> None:
        self._utxos = values if isinstance(values, TrackedUTXODict) else TrackedUTXODict(values)

    def get_utxo(self, txid: str, vout: int) -> Optional[UTXOEntry]:
        return self.utxos.get((txid, vout))

    def has_utxo(self, txid: str, vout: int) -> bool:
        return (txid, vout) in self.utxos

    def compute_tx_sighash(self, tx: StealthTransaction) -> bytes:
        """Deterministic transaction digest for signing and verification"""
        return tx.compute_sighash()

    def validate_transaction(
        self,
        tx: StealthTransaction,
        current_height: int,
        utxos: Optional[Dict[Tuple[str, int], UTXOEntry]] = None,
    ) -> Tuple[bool, str, int]:
        """
        Validates a transaction against current UTXO set.
        Returns (is_valid, error_msg, fee_satk).
        """
        view = self.utxos if utxos is None else utxos
        if not tx.inputs:
            return False, "Transaction has no inputs", 0
        if not tx.outputs:
            return False, "Transaction has no outputs", 0

        total_in = 0
        seen_inputs: Set[Tuple[str, int]] = set()

        for inp in tx.inputs:
            outpoint = (inp.prev_txid, inp.prev_vout)
            if outpoint in seen_inputs:
                return False, f"Duplicate input in transaction: {outpoint}", 0
            seen_inputs.add(outpoint)

            utxo = view.get(outpoint)
            if not utxo:
                return False, f"Referenced UTXO does not exist or already spent: {outpoint}", 0
            if utxo.kind == "jackpot_pool":
                return False, f"Protocol jackpot output cannot be spent by a transaction: {outpoint}", 0

            # Streaming timelock enforcement (Combo jackpot tranches)
            if utxo.timelock_until_height > 0:
                if current_height < utxo.timelock_until_height:
                    return False, f"Output timelocked until height {utxo.timelock_until_height}, current height {current_height}", 0
            elif utxo.is_coinbase:
                # Progressive Regular Coinbase maturity enforcement
                req_maturity = get_regular_coinbase_maturity_blocks(utxo.block_height)
                confirmations = current_height - utxo.block_height
                if confirmations < req_maturity:
                    return False, f"Coinbase maturity violation: {confirmations} / {req_maturity} confirmations", 0

            # Verify input amount matches UTXO
            if inp.amount_satk != utxo.amount_satk:
                return False, f"Input amount {inp.amount_satk} does not match UTXO amount {utxo.amount_satk}", 0

            # Cryptographic signature verification
            if not inp.signature_hex:
                return False, "Missing signature on input", 0

            sighash = self.compute_tx_sighash(tx)
            try:
                pub_bytes = bytes.fromhex(utxo.P_onetime_hex)
                sig_bytes = bytes.fromhex(inp.signature_hex)
                if not schnorr_verify(pub_bytes, sighash, sig_bytes):
                    return False, f"Invalid Schnorr signature for input {outpoint}", 0
            except Exception as e:
                return False, f"Malformed signature or public key on input {outpoint}: {e}", 0

            total_in += utxo.amount_satk

        total_out = 0
        for out in tx.outputs:
            if out.amount_satk <= 0:
                return False, f"Output amount must be positive, got {out.amount_satk}", 0
            total_out += out.amount_satk

        if total_in < total_out:
            return False, f"Value conservation violated: inputs ({total_in}) < outputs ({total_out})", 0

        fee_satk = total_in - total_out
        return True, "", fee_satk

    def preview_block_fees(
        self,
        transactions: List[StealthTransaction],
        block_height: int,
        allow_transaction_skips: bool = False,
    ) -> int:
        """Validate a block transaction sequence without mutating consensus state."""
        # Mining-only blocks have exactly zero transaction fees. Do not copy
        # the lifetime UTXO map just to iterate an empty transaction sequence.
        # Nonempty/other input types retain the existing validation path.
        if isinstance(transactions, (list, tuple)) and not transactions:
            return 0
        overlay = dict(self.utxos)
        total_fees_sats = 0
        for tx in transactions:
            valid, error, fee_sats = self.validate_transaction(tx, block_height, overlay)
            if not valid:
                if allow_transaction_skips:
                    continue
                raise ValueError(f"invalid transaction {tx.txid}: {error}")
            candidate = dict(overlay)
            for tx_input in tx.inputs:
                candidate.pop((tx_input.prev_txid, tx_input.prev_vout))
            duplicate = False
            for index, output in enumerate(tx.outputs):
                outpoint = (tx.txid, index)
                if not tx.txid or outpoint in candidate:
                    duplicate = True
                    break
                candidate[outpoint] = UTXOEntry(
                    amount_satk=output.amount_satk,
                    P_onetime_hex=output.P_onetime_hex,
                    R_ephem_hex=output.R_ephem_hex,
                    block_height=block_height,
                )
            if duplicate:
                if allow_transaction_skips:
                    continue
                raise ValueError(f"Duplicate or empty transaction outpoint in {tx.txid}")
            overlay = candidate
            total_fees_sats += fee_sats
        return total_fees_sats

    def apply_block(
        self,
        block_hash: str,
        block_height: int,
        coinbase_output: Tuple[bytes, bytes],
        reward_sats: int,
        transactions: List[StealthTransaction],
        vesting_outputs: Optional[List[Dict[str, Any]]] = None,
        eco_output: Optional[Tuple[int, bytes, bytes]] = None,
        pool_deposit_sats: int = 0,
        pool_payout_sats: int = 0,
        expected_miner_fee_sats: Optional[int] = None,
        fee_pool_deposit_sats: int = 0,
        allow_transaction_skips: bool = False,
    ) -> Tuple[bool, str]:
        """
        Applies a block's coinbase output and transactions to the UTXO set atomically.
        """
        if block_hash in self._block_spent_undo or block_hash in self._block_created_undo:
            return False, f"Block {block_hash} was already applied"

        spent_undo: List[Tuple[Tuple[str, int], UTXOEntry]] = []
        created_undo: List[Tuple[str, int]] = []

        # Empty mining blocks dominate the live chain. They only append the
        # bounded protocol outputs below, so copying the complete accumulated
        # UTXO map before every block is unnecessary. Validate all additions
        # first and commit them together while preserving the same undo data.
        if not transactions and pool_payout_sats == 0 and not vesting_outputs:
            additions: Dict[Tuple[str, int], UTXOEntry] = {}

            def stage(outpoint, entry):
                if outpoint in self.utxos or outpoint in additions:
                    return False
                additions[outpoint] = entry
                created_undo.append(outpoint)
                return True

            if reward_sats > 0:
                cb_outpoint = (f"cb_{block_hash}", 0)
                if not stage(cb_outpoint, UTXOEntry(
                    amount_satk=reward_sats,
                    P_onetime_hex=coinbase_output[1].hex(),
                    R_ephem_hex=coinbase_output[0].hex(),
                    block_height=block_height,
                    is_coinbase=True,
                    kind="miner",
                )):
                    return False, f"Duplicate coinbase outpoint: {cb_outpoint}"
            if eco_output and eco_output[0] > 0:
                eco_amount, eco_r, eco_p = eco_output
                eco_outpoint = (f"eco_{block_hash}", 0)
                if not stage(eco_outpoint, UTXOEntry(
                    amount_satk=eco_amount,
                    P_onetime_hex=eco_p.hex(),
                    R_ephem_hex=eco_r.hex(),
                    block_height=block_height,
                    is_coinbase=True,
                    kind="eco",
                )):
                    return False, f"Duplicate ecosystem outpoint: {eco_outpoint}"
            if pool_deposit_sats < 0:
                return False, "Jackpot pool amounts cannot be negative"
            if pool_deposit_sats > 0:
                pool_outpoint = (f"pool_{block_hash}", 0)
                if not stage(pool_outpoint, UTXOEntry(
                    amount_satk=pool_deposit_sats,
                    P_onetime_hex="",
                    R_ephem_hex="",
                    block_height=block_height,
                    is_coinbase=True,
                    kind="jackpot_pool",
                )):
                    return False, f"Duplicate jackpot pool outpoint: {pool_outpoint}"

            self.utxos.update(additions)
            self._block_spent_undo[block_hash] = []
            self._block_created_undo[block_hash] = created_undo
            self._block_fees[block_hash] = 0
            self._block_accepted_txids[block_hash] = []
            return True, "Block applied successfully"

        overlay = dict(self.utxos)
        total_fees_sats = 0
        accepted_txids: List[str] = []

        # Validate and stage against an overlay. This catches cross-transaction
        # double spends and guarantees the live map is untouched on failure.
        for tx in transactions:
            valid, err, fee_sats = self.validate_transaction(tx, block_height, overlay)
            if not valid:
                if allow_transaction_skips:
                    continue
                return False, f"Block rejected due to invalid transaction {tx.txid}: {err}"
            tx_overlay = dict(overlay)
            tx_spent: List[Tuple[Tuple[str, int], UTXOEntry]] = []
            tx_created: List[Tuple[str, int]] = []
            for inp in tx.inputs:
                outpoint = (inp.prev_txid, inp.prev_vout)
                utxo = tx_overlay.pop(outpoint)
                tx_spent.append((outpoint, utxo))

            for vout_idx, out in enumerate(tx.outputs):
                outpoint = (tx.txid, vout_idx)
                if not tx.txid or outpoint in tx_overlay:
                    if allow_transaction_skips:
                        tx_overlay = None
                        break
                    return False, f"Duplicate or empty transaction outpoint: {outpoint}"
                entry = UTXOEntry(
                    amount_satk=out.amount_satk,
                    P_onetime_hex=out.P_onetime_hex,
                    R_ephem_hex=out.R_ephem_hex,
                    block_height=block_height,
                    is_coinbase=False,
                    kind="standard",
                )
                tx_overlay[outpoint] = entry
                tx_created.append(outpoint)
            if tx_overlay is None:
                continue
            overlay = tx_overlay
            spent_undo.extend(tx_spent)
            created_undo.extend(tx_created)
            total_fees_sats += fee_sats
            accepted_txids.append(tx.txid)

        # 3. Add Coinbase UTXO (if reward > 0)
        if expected_miner_fee_sats is None:
            expected_miner_fee_sats = total_fees_sats
        if expected_miner_fee_sats < 0 or fee_pool_deposit_sats < 0:
            return False, "Fee split amounts cannot be negative"
        if expected_miner_fee_sats + fee_pool_deposit_sats != total_fees_sats:
            return False, "Consensus fee split does not equal validated transaction fees"
        miner_payout_sats = reward_sats + expected_miner_fee_sats
        if miner_payout_sats > 0:
            cb_outpoint = (f"cb_{block_hash}", 0)
            if cb_outpoint in overlay:
                return False, f"Duplicate coinbase outpoint: {cb_outpoint}"
            cb_entry = UTXOEntry(
                amount_satk=miner_payout_sats,
                P_onetime_hex=coinbase_output[1].hex(),
                R_ephem_hex=coinbase_output[0].hex(),
                block_height=block_height,
                is_coinbase=True,
                kind="miner",
            )
            overlay[cb_outpoint] = cb_entry
            created_undo.append(cb_outpoint)

        # Explicit ecosystem reserve output.
        if eco_output and eco_output[0] > 0:
            eco_amount, eco_r, eco_p = eco_output
            eco_outpoint = (f"eco_{block_hash}", 0)
            if eco_outpoint in overlay:
                return False, f"Duplicate ecosystem outpoint: {eco_outpoint}"
            overlay[eco_outpoint] = UTXOEntry(
                amount_satk=eco_amount,
                P_onetime_hex=eco_p.hex(),
                R_ephem_hex=eco_r.hex(),
                block_height=block_height,
                is_coinbase=True,
                kind="eco",
            )
            created_undo.append(eco_outpoint)

        # Explicit jackpot reserve output. It is protocol-controlled and cannot
        # be spent by a normal signed transaction.
        if pool_deposit_sats < 0 or pool_payout_sats < 0:
            return False, "Jackpot pool amounts cannot be negative"
        if pool_deposit_sats > 0:
            pool_outpoint = (f"pool_{block_hash}", 0)
            if pool_outpoint in overlay:
                return False, f"Duplicate jackpot pool outpoint: {pool_outpoint}"
            overlay[pool_outpoint] = UTXOEntry(
                amount_satk=pool_deposit_sats,
                P_onetime_hex="",
                R_ephem_hex="",
                block_height=block_height,
                is_coinbase=True,
                kind="jackpot_pool",
            )
            created_undo.append(pool_outpoint)

        if pool_payout_sats > 0:
            remaining = pool_payout_sats
            pool_entries = sorted(
                (
                    (outpoint, entry)
                    for outpoint, entry in overlay.items()
                    if entry.kind == "jackpot_pool"
                ),
                key=lambda item: (item[1].block_height, item[0]),
            )
            for outpoint, entry in pool_entries:
                if remaining <= 0:
                    break
                if entry.amount_satk > remaining:
                    return False, "Jackpot payout does not align with frozen pool deposits"
                overlay.pop(outpoint)
                spent_undo.append((outpoint, entry))
                remaining -= entry.amount_satk
            if remaining != 0:
                return False, "Jackpot pool UTXOs are insufficient for payout"

        # Add resolved, timelocked jackpot outputs.
        vesting_total = sum(int(vo.get("amount_satk", 0)) for vo in (vesting_outputs or []))
        if vesting_total != pool_payout_sats:
            return False, "Jackpot vesting outputs do not equal the pool payout"
        if vesting_outputs:
            for vo in vesting_outputs:
                vo_outpoint = (f"vesting_{block_hash}_{vo['tranche_index']}", 0)
                if vo_outpoint in overlay:
                    return False, f"Duplicate vesting outpoint: {vo_outpoint}"
                vo_entry = UTXOEntry(
                    amount_satk=vo["amount_satk"],
                    P_onetime_hex=vo.get("P_onetime_hex", coinbase_output[1].hex()),
                    R_ephem_hex=vo.get("R_ephem_hex", coinbase_output[0].hex()),
                    block_height=block_height,
                    is_coinbase=True,
                    timelock_until_height=vo.get("unlock_block_height", 0),
                    kind="vesting",
                )
                overlay[vo_outpoint] = vo_entry
                created_undo.append(vo_outpoint)

        # Single commit point.
        self.utxos = overlay
        self._block_spent_undo[block_hash] = spent_undo
        self._block_created_undo[block_hash] = created_undo
        self._block_fees[block_hash] = total_fees_sats
        self._block_accepted_txids[block_hash] = accepted_txids
        return True, "Block applied successfully"

    def revert_block(self, block_hash: str):
        """Rollback a block during DAG reorganization"""
        created = self._block_created_undo.pop(block_hash, [])
        for outpoint in created:
            self.utxos.pop(outpoint, None)

        spent = self._block_spent_undo.pop(block_hash, [])
        for outpoint, entry in spent:
            self.utxos[outpoint] = entry
        self._block_fees.pop(block_hash, None)
        self._block_accepted_txids.pop(block_hash, None)

    def get_block_fees(self, block_hash: str) -> int:
        return self._block_fees.get(block_hash, 0)

    def get_block_accepted_txids(self, block_hash: str) -> List[str]:
        return list(self._block_accepted_txids.get(block_hash, []))

    def total_value_sats(self) -> int:
        return self.utxos.total_satk

    def value_by_kind_sats(self, kind: str) -> int:
        return self.utxos.kind_totals.get(kind, 0)

if __name__ == "__main__":
    print("Self-testing UTXOSet...")
    u = UTXOSet()
    # Add dummy UTXO
    outpoint = ("tx_test_1", 0)
    u.utxos[outpoint] = UTXOEntry(
        amount_satk=1000000,
        P_onetime_hex="02" + "11"*32,
        R_ephem_hex="02" + "22"*32,
        block_height=10,
        is_coinbase=True
    )
    assert u.has_utxo("tx_test_1", 0)
    print("✓ UTXOSet basic operations work.")
