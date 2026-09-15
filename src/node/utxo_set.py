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

COINBASE_MATURITY_CONFIRMATIONS = 120

@dataclass
class UTXOEntry:
    amount_satk: int
    P_onetime_hex: str
    R_ephem_hex: str
    block_height: int
    is_coinbase: bool = False
    timelock_until_height: int = 0

class UTXOSet:
    """
    Consensus UTXO Set tracking all spendable outputs across the BlockDAG.
    """
    def __init__(self):
        # Outpoint (txid, vout) -> UTXOEntry
        self.utxos: Dict[Tuple[str, int], UTXOEntry] = {}
        # Block hash -> list of (spent_outpoint, UTXOEntry) for rollbacks
        self._block_spent_undo: Dict[str, List[Tuple[Tuple[str, int], UTXOEntry]]] = {}
        # Block hash -> list of created outpoints for rollbacks
        self._block_created_undo: Dict[str, List[Tuple[str, int]]] = {}

    def get_utxo(self, txid: str, vout: int) -> Optional[UTXOEntry]:
        return self.utxos.get((txid, vout))

    def has_utxo(self, txid: str, vout: int) -> bool:
        return (txid, vout) in self.utxos

    def compute_tx_sighash(self, tx: StealthTransaction) -> bytes:
        """Deterministic transaction digest for signing and verification"""
        inp_str = "".join(f"{inp.prev_txid}:{inp.prev_vout}:{inp.amount_satk}:{inp.P_onetime_hex}" for inp in tx.inputs)
        out_str = "".join(f"{out.amount_satk}:{out.R_ephem_hex}:{out.P_onetime_hex}" for out in tx.outputs)
        raw = f"{tx.version}_{inp_str}_{out_str}_{tx.lock_time}".encode('utf-8')
        return sha256(raw)

    def validate_transaction(self, tx: StealthTransaction, current_height: int) -> Tuple[bool, str, int]:
        """
        Validates a transaction against current UTXO set.
        Returns (is_valid, error_msg, fee_satk).
        """
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

            utxo = self.utxos.get(outpoint)
            if not utxo:
                return False, f"Referenced UTXO does not exist or already spent: {outpoint}", 0

            # 120-block Coinbase maturity enforcement
            if utxo.is_coinbase:
                confirmations = current_height - utxo.block_height
                if confirmations < COINBASE_MATURITY_CONFIRMATIONS:
                    return False, f"Coinbase maturity violation: {confirmations} / {COINBASE_MATURITY_CONFIRMATIONS} confirmations", 0

            # Streaming timelock enforcement
            if utxo.timelock_until_height > 0 and current_height < utxo.timelock_until_height:
                return False, f"Output timelocked until height {utxo.timelock_until_height}, current height {current_height}", 0

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

    def apply_block(
        self,
        block_hash: str,
        block_height: int,
        coinbase_output: Tuple[bytes, bytes],
        reward_sats: int,
        transactions: List[StealthTransaction],
        vesting_outputs: Optional[List[Dict[str, Any]]] = None
    ) -> Tuple[bool, str]:
        """
        Applies a block's coinbase output and transactions to the UTXO set atomically.
        """
        spent_undo: List[Tuple[Tuple[str, int], UTXOEntry]] = []
        created_undo: List[Tuple[str, int]] = []

        # 1. Validate all transactions first (atomic check)
        for tx in transactions:
            valid, err, _ = self.validate_transaction(tx, block_height)
            if not valid:
                return False, f"Block rejected due to invalid transaction {tx.txid}: {err}"

        # 2. Spend transaction inputs
        for tx in transactions:
            for inp in tx.inputs:
                outpoint = (inp.prev_txid, inp.prev_vout)
                utxo = self.utxos.pop(outpoint)
                spent_undo.append((outpoint, utxo))

            # Add transaction outputs
            for vout_idx, out in enumerate(tx.outputs):
                outpoint = (tx.txid, vout_idx)
                entry = UTXOEntry(
                    amount_satk=out.amount_satk,
                    P_onetime_hex=out.P_onetime_hex,
                    R_ephem_hex=out.R_ephem_hex,
                    block_height=block_height,
                    is_coinbase=False
                )
                self.utxos[outpoint] = entry
                created_undo.append(outpoint)

        # 3. Add Coinbase UTXO (if reward > 0)
        if reward_sats > 0:
            cb_outpoint = (f"cb_{block_hash}", 0)
            cb_entry = UTXOEntry(
                amount_satk=reward_sats,
                P_onetime_hex=coinbase_output[1].hex(),
                R_ephem_hex=coinbase_output[0].hex(),
                block_height=block_height,
                is_coinbase=True
            )
            self.utxos[cb_outpoint] = cb_entry
            created_undo.append(cb_outpoint)

        # 4. Add Vesting Outputs (e.g. streaming lottery payouts)
        if vesting_outputs:
            for vo in vesting_outputs:
                vo_outpoint = (f"vesting_{block_hash}_{vo['tranche_index']}", 0)
                vo_entry = UTXOEntry(
                    amount_satk=vo["amount_satk"],
                    P_onetime_hex=vo.get("P_onetime_hex", coinbase_output[1].hex()),
                    R_ephem_hex=vo.get("R_ephem_hex", coinbase_output[0].hex()),
                    block_height=block_height,
                    is_coinbase=True,
                    timelock_until_height=vo.get("unlock_block_height", 0)
                )
                self.utxos[vo_outpoint] = vo_entry
                created_undo.append(vo_outpoint)

        self._block_spent_undo[block_hash] = spent_undo
        self._block_created_undo[block_hash] = created_undo
        return True, "Block applied successfully"

    def revert_block(self, block_hash: str):
        """Rollback a block during DAG reorganization"""
        created = self._block_created_undo.pop(block_hash, [])
        for outpoint in created:
            self.utxos.pop(outpoint, None)

        spent = self._block_spent_undo.pop(block_hash, [])
        for outpoint, entry in spent:
            self.utxos[outpoint] = entry

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
