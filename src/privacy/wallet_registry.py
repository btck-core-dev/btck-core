"""Local, non-secret index for independently encrypted BTC-K wallets."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path


REGISTRY_FORMAT = "btck-wallet-registry-v1"
DEFAULT_REGISTRY_PATH = (
    Path.home() / "Library" / "Application Support" / "Bitcoin K Wallet" / "wallets.json"
)


class WalletRegistryError(ValueError):
    pass


class WalletRegistry:
    """Index wallet paths and labels without storing keys or passwords."""

    def __init__(self, path: Path | str = DEFAULT_REGISTRY_PATH):
        self.path = Path(path).expanduser()
        self.records: list[dict] = []
        self.load()

    @staticmethod
    def _canonical_path(path: Path | str) -> Path:
        return Path(path).expanduser().resolve()

    @staticmethod
    def _wallet_id(path: Path) -> str:
        return hashlib.sha256(str(path).encode("utf-8")).hexdigest()[:24]

    @staticmethod
    def _validated_label(label: str) -> str:
        value = " ".join(str(label).strip().split())
        if not value:
            raise WalletRegistryError("Wallet display name cannot be empty")
        if len(value) > 64:
            raise WalletRegistryError("Wallet display name cannot exceed 64 characters")
        return value

    def load(self) -> None:
        if not self.path.exists():
            self.records = []
            return
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise WalletRegistryError(f"Cannot read wallet registry: {exc}") from exc
        if not isinstance(payload, dict) or payload.get("format") != REGISTRY_FORMAT:
            raise WalletRegistryError("Wallet registry has an unknown format")
        raw_records = payload.get("wallets")
        if not isinstance(raw_records, list) or len(raw_records) > 100:
            raise WalletRegistryError("Wallet registry contains an invalid wallet list")
        validated = []
        seen_ids = set()
        for item in raw_records:
            if not isinstance(item, dict):
                raise WalletRegistryError("Wallet registry entry must be an object")
            wallet_path = self._canonical_path(item.get("path", ""))
            wallet_id = self._wallet_id(wallet_path)
            if item.get("id") != wallet_id or wallet_id in seen_ids:
                raise WalletRegistryError("Wallet registry entry identity is invalid or duplicated")
            network = str(item.get("network", ""))
            if network != "mainnet":
                raise WalletRegistryError("Wallet registry entry is not BTC-K mainnet")
            validated.append({
                "id": wallet_id,
                "label": self._validated_label(item.get("label", "")),
                "path": str(wallet_path),
                "network": "mainnet",
                "account_model": "independent",
            })
            seen_ids.add(wallet_id)
        self.records = validated

    def _save(self) -> None:
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.path.parent.chmod(0o700)
        payload = {"format": REGISTRY_FORMAT, "wallets": self.records}
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".wallets.", suffix=".tmp", dir=self.path.parent,
        )
        temporary = Path(temporary_name)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
            self.path.chmod(0o600)
        finally:
            if temporary.exists():
                temporary.unlink()

    def add(self, wallet_path: Path | str, label: str) -> dict:
        path = self._canonical_path(wallet_path)
        record = {
            "id": self._wallet_id(path),
            "label": self._validated_label(label),
            "path": str(path),
            "network": "mainnet",
            "account_model": "independent",
        }
        for index, existing in enumerate(self.records):
            if existing["id"] == record["id"]:
                self.records[index] = record
                self._save()
                return dict(record)
        self.records.append(record)
        self._save()
        return dict(record)

    def rename(self, wallet_id: str, label: str) -> dict:
        for record in self.records:
            if record["id"] == wallet_id:
                record["label"] = self._validated_label(label)
                self._save()
                return dict(record)
        raise WalletRegistryError("Wallet is not present in the local registry")

    def remove(self, wallet_id: str) -> None:
        retained = [record for record in self.records if record["id"] != wallet_id]
        if len(retained) == len(self.records):
            raise WalletRegistryError("Wallet is not present in the local registry")
        self.records = retained
        self._save()

    def get(self, wallet_id: str) -> dict | None:
        return next((dict(record) for record in self.records if record["id"] == wallet_id), None)

