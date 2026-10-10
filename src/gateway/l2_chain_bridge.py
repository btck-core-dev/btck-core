"""Fail-closed L1 deposit, withdrawal, and provable-burn bridge for L2 SatK."""

import asyncio
from typing import Any, Dict, Optional

from privacy.stealth_address import StealthKeyPair
from privacy.wallet_engine import (
    StealthWallet,
    transaction_from_dict,
    transaction_to_dict,
)
from worker.pricing_engine import TieredPricingEngine
L2_FEE_RESERVE_ACCOUNT = "btck:l2:fee-reserve"


class L2ChainBridge:
    """Custodial bridge primitive; no HTTP endpoint is enabled by this class."""

    def __init__(
        self,
        node_rpc,
        treasury_wallet: StealthWallet,
        ledger,
        expected_chain_id: str,
        trusted_funding_outpoints=(),
        settlement_confirmations: int = 10,
    ):
        if node_rpc is None or treasury_wallet is None:
            raise ValueError("node RPC and an explicit persistent treasury wallet are required")
        self.node_rpc = node_rpc
        self.treasury_wallet = treasury_wallet
        self.ledger = ledger
        self.expected_chain_id = expected_chain_id
        self.settlement_confirmations = int(settlement_confirmations)
        if self.settlement_confirmations < 1:
            raise ValueError("settlement_confirmations must be at least 1")
        self.trusted_funding_outpoints = {
            (str(txid), int(vout)) for txid, vout in trusted_funding_outpoints
        }
        self._lock = asyncio.Lock()

    async def chain_context(self):
        info = await self.node_rpc.call("getInfo")
        chain_id = str(info.get("network", {}).get("chain_id", ""))
        if chain_id != self.expected_chain_id:
            raise RuntimeError(f"chain mismatch: expected {self.expected_chain_id}, got {chain_id or 'missing'}")
        length = info.get("dag", {}).get("virtual_blue_order_length")
        if not isinstance(length, int) or isinstance(length, bool) or length < 1:
            raise RuntimeError("node returned invalid virtual-blue order length")
        height = length - 1
        return height, TieredPricingEngine.network_year_for_height(height)

    async def _sync_treasury(self, height: int) -> int:
        snapshot = await self.node_rpc.call("getUTXOSet")
        self.treasury_wallet.utxos = []
        self.treasury_wallet.scan_chain_outputs(snapshot.get("outputs", []))
        allowed = set(self.trusted_funding_outpoints)
        for receipt_key in self.ledger.processed_receipts:
            txid, separator, vout = str(receipt_key).partition(":")
            if separator and len(txid) == 64:
                try:
                    allowed.add((txid, int(vout)))
                except ValueError:
                    pass
        # Change outputs created by this bridge remain valid backing after the
        # source deposit UTXOs are spent and the transactions confirm.
        for record in list(self.ledger.l2_withdrawals.values()) + list(self.ledger.l2_burn_records.values()):
            transaction = record.get("transaction", {})
            txid = transaction.get("txid", "")
            for vout, output in enumerate(transaction.get("outputs", [])):
                try:
                    belongs = self.treasury_wallet.keypair.is_my_stealth_output(
                        bytes.fromhex(output.get("R_ephem_hex", "")),
                        bytes.fromhex(output.get("P_onetime_hex", "")),
                    )
                except (TypeError, ValueError):
                    belongs = False
                if belongs:
                    allowed.add((txid, vout))
        for utxo in self.treasury_wallet.utxos:
            if (utxo.txid, utxo.vout) not in allowed:
                utxo.spent = True
        # Prevent a restart from selecting inputs already committed by a signed
        # but not-yet-confirmed withdrawal or burn transaction.
        committed = set()
        records = list(self.ledger.l2_withdrawals.values()) + list(self.ledger.l2_burn_records.values())
        for record in records:
            if record.get("status") in {"SIGNED", "BROADCAST", "CONFIRMED"}:
                for tx_input in record.get("transaction", {}).get("inputs", []):
                    committed.add((tx_input.get("prev_txid"), int(tx_input.get("prev_vout", -1))))
        for utxo in self.treasury_wallet.utxos:
            if (utxo.txid, utxo.vout) in committed:
                utxo.spent = True
        return self.treasury_wallet.get_spendable_balance_satk(height)

    async def _broadcast_record(self, record: Dict[str, Any], update):
        tx = transaction_from_dict(record["transaction"])
        status = await self.node_rpc.call("getTransaction", {"txid": tx.txid})
        if status.get("status") == "confirmed":
            confirmations = int(status.get("confirmations", 0))
            return update(
                status=(
                    "CONFIRMED"
                    if confirmations >= self.settlement_confirmations
                    else "BROADCAST"
                ),
                txid=tx.txid,
                confirmations=confirmations,
                block_hash=status.get("block_hash"),
            )
        if status.get("status") == "mempool":
            return update(status="BROADCAST", txid=tx.txid, confirmations=0)
        result = await self.node_rpc.call("submitTransaction", {"transaction": transaction_to_dict(tx)})
        if not result.get("accepted"):
            raise RuntimeError(f"node rejected transaction: {result}")
        return update(status="BROADCAST", txid=tx.txid)

    async def withdraw(
        self,
        withdrawal_id: str,
        payer: str,
        destination: str,
        amount_satk: int,
        fee_satk: int,
        fee_payer_account: str = L2_FEE_RESERVE_ACCOUNT,
    ) -> Optional[Dict[str, Any]]:
        if not StealthKeyPair.validate_stealth_address(destination):
            raise ValueError("invalid BTC-K withdrawal destination")
        async with self._lock:
            record = self.ledger.reserve_withdrawal(
                withdrawal_id, payer, destination, amount_satk, fee_satk,
                fee_payer_account,
            )
            if record is None:
                return None
            if record.get("status") in {"CONFIRMED", "FAILED_REFUNDED"}:
                return record
            if record.get("status") in {"SIGNED", "BROADCAST"}:
                try:
                    return await self._broadcast_record(
                        record,
                        lambda **updates: self.ledger.update_withdrawal(withdrawal_id, **updates),
                    )
                except Exception:
                    # Unknown-effect state: retain the signed transaction for
                    # deterministic reconciliation; never guess and refund.
                    raise

            try:
                height, network_year = await self.chain_context()
                await self._sync_treasury(height)
                tx = self.treasury_wallet.create_and_sign_transfer(
                    destination,
                    amount_satk,
                    fee_satk=fee_satk,
                    current_height=height,
                )
            except Exception as exc:
                return self.ledger.refund_withdrawal(withdrawal_id, str(exc))
            record = self.ledger.update_withdrawal(
                withdrawal_id,
                status="SIGNED",
                network_height=height,
                network_year=network_year,
                transaction=transaction_to_dict(tx),
                txid=tx.txid,
            )
            return await self._broadcast_record(
                record,
                lambda **updates: self.ledger.update_withdrawal(withdrawal_id, **updates),
            )

    async def broadcast_burn(
        self,
        burn_id: str,
        amount_satk: int,
        fee_satk: int,
        fee_payer_account: str,
    ) -> Optional[Dict[str, Any]]:
        async with self._lock:
            record = self.ledger.reserve_burn(
                burn_id, amount_satk, fee_satk, fee_payer_account
            )
            if record is None:
                return None
            if record.get("status") == "CONFIRMED":
                return record
            if record.get("status") in {"SIGNED", "BROADCAST"}:
                return await self._broadcast_record(
                    record,
                    lambda **updates: self.ledger.update_burn(burn_id, **updates),
                )
            try:
                height, network_year = await self.chain_context()
                await self._sync_treasury(height)
                tx = self.treasury_wallet.create_and_sign_provable_burn(
                    amount_satk,
                    fee_satk=fee_satk,
                    current_height=height,
                )
            except Exception as exc:
                return self.ledger.refund_burn(burn_id, str(exc))
            record = self.ledger.update_burn(
                burn_id,
                status="SIGNED",
                network_height=height,
                network_year=network_year,
                transaction=transaction_to_dict(tx),
                txid=tx.txid,
            )
            return await self._broadcast_record(
                record,
                lambda **updates: self.ledger.update_burn(burn_id, **updates),
            )
