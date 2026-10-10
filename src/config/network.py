"""Validated, network-scoped runtime parameters.

The selected network is a consensus input.  It controls the chain identity,
wire magic, genesis block and storage namespace.  Mainnet is deliberately
unavailable until its checked-in manifest has passed the release gate.
"""

from __future__ import annotations

import json
import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MANIFEST_DIR = PROJECT_ROOT / "params" / "networks"
SUPPORTED_NETWORKS = frozenset({"simnet", "devnet", "testnet", "mainnet"})
_HEX_8 = re.compile(r"^[0-9a-fA-F]{8}$")
_HEX_64 = re.compile(r"^[0-9a-fA-F]{64}$")
_HEX_66 = re.compile(r"^[0-9a-fA-F]{66}$")


class NetworkConfigurationError(ValueError):
    """A network manifest is missing, inconsistent or unsafe."""


class MainnetReleaseError(NetworkConfigurationError):
    """Mainnet startup was requested before the release manifest was approved."""


class DisabledFeatureError(NetworkConfigurationError):
    """An unsafe or unfinished feature was requested without manifest approval."""


@dataclass(frozen=True)
class GenesisConfig:
    version: int
    timestamp: float
    bits: int
    nonce: int
    headline: str
    coinbase_output: tuple[bytes, bytes]
    expected_hash: str


@dataclass(frozen=True)
class NetworkConfig:
    manifest_version: int
    name: str
    chain_id: str
    p2p_magic: str
    data_subdir: str
    block_version: int
    pow_limit_bits: int
    max_future_seconds: int
    max_parents: int
    ghostdag_k: int
    max_merge_set_size: int
    daa_window_blocks: int
    daa_initial_bits: int
    daa_target_interval_ms: int
    daa_median_time_blocks: int
    daa_max_adjustment_factor: int
    daa_progress: str
    eco_fund_output: tuple[bytes, bytes]
    hard_checkpoints: Mapping[int, str]
    genesis: GenesisConfig
    features: Mapping[str, bool]
    manifest_path: Path
    release_approved: bool
    release_approval_id: str
    consensus_fingerprint: str

    @property
    def is_mainnet(self) -> bool:
        return self.name == "mainnet"

    def data_dir(self, root: Optional[Path | str] = None) -> Path:
        """Return a storage path that can never alias another network."""
        configured_root = root if root is not None else os.getenv("BTCK_DATA_ROOT")
        base = Path(configured_root).expanduser().resolve() if configured_root else PROJECT_ROOT / "data" / "networks"
        return base / self.data_subdir

    def feature_enabled(self, feature: str) -> bool:
        return bool(self.features.get(feature, False))


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise NetworkConfigurationError(message)


def _parse_manifest(path: Path, requested_name: str) -> NetworkConfig:
    try:
        raw: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise NetworkConfigurationError(f"Network manifest not found: {path}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise NetworkConfigurationError(f"Cannot read network manifest {path}: {exc}") from exc

    _require(raw.get("manifest_version") == 1, "Unsupported network manifest version")
    _require(raw.get("network") == requested_name, "Manifest network does not match requested network")
    _require(raw.get("network") in SUPPORTED_NETWORKS, "Unsupported BTC-K network")

    chain_id = raw.get("chain_id")
    p2p_magic = raw.get("p2p_magic")
    data_subdir = raw.get("data_subdir")
    _require(isinstance(chain_id, str) and len(chain_id) >= 8, "chain_id must be a stable non-empty identifier")
    _require(isinstance(p2p_magic, str) and bool(_HEX_8.fullmatch(p2p_magic)), "p2p_magic must be exactly four bytes of hex")
    _require(data_subdir == requested_name, "data_subdir must equal the network name")

    genesis_raw = raw.get("genesis", {})
    outputs = genesis_raw.get("coinbase_output", [])
    _require(isinstance(outputs, list) and len(outputs) == 2, "Genesis must have exactly two coinbase output points")
    _require(all(isinstance(value, str) and _HEX_66.fullmatch(value) for value in outputs), "Genesis coinbase points must be 33-byte hex strings")
    expected_hash = genesis_raw.get("expected_hash", "")
    _require(isinstance(expected_hash, str) and bool(_HEX_64.fullmatch(expected_hash)), "Genesis expected_hash must be 32-byte hex")

    consensus = raw.get("consensus", {})
    _require(isinstance(consensus, dict), "consensus must be an object")
    block_version = int(consensus.get("block_version", 0))
    pow_limit_bits = int(str(consensus.get("pow_limit_bits", "0")), 0)
    max_future_seconds = int(consensus.get("max_future_seconds", 0))
    max_parents = int(consensus.get("max_parents", 0))
    ghostdag_k = int(consensus.get("ghostdag_k", -1))
    max_merge_set_size = int(consensus.get("max_merge_set_size", 0))
    daa = consensus.get("daa", {})
    _require(isinstance(daa, dict), "consensus.daa must be an object")
    daa_window_blocks = int(daa.get("window_blocks", 0))
    daa_initial_bits = int(str(daa.get("initial_bits", "0")), 0)
    daa_target_interval_ms = int(daa.get("target_interval_ms", 0))
    daa_median_time_blocks = int(daa.get("median_time_blocks", 0))
    daa_max_adjustment_factor = int(daa.get("max_adjustment_factor", 0))
    daa_progress = str(daa.get("progress", "selected-chain-v1"))
    _require(block_version >= 2, "block_version must use the post-audit v2 header or later")
    _require(0 < pow_limit_bits <= 0xFFFFFFFF, "pow_limit_bits must be a uint32 compact target")
    _require(0 < max_future_seconds <= 7200, "max_future_seconds is outside the safe range")
    _require(0 < max_parents <= 64, "max_parents is outside the safe range")
    _require(0 <= ghostdag_k <= 64, "ghostdag_k is outside the safe range")
    _require(0 < max_merge_set_size <= 4096, "max_merge_set_size is outside the safe range")
    _require(16 <= daa_window_blocks <= 4096, "DAA window_blocks is outside the safe range")
    _require(0 < daa_initial_bits <= 0xFFFFFFFF, "DAA initial_bits must be a uint32 compact target")
    _require(100 <= daa_target_interval_ms <= 600_000, "DAA target interval is outside the safe range")
    _require(3 <= daa_median_time_blocks <= 101 and daa_median_time_blocks % 2 == 1, "DAA median window must be odd")
    _require(2 <= daa_max_adjustment_factor <= 16, "DAA adjustment factor is outside the safe range")
    _require(
        daa_progress in {"selected-chain-v1", "blue-score-v1"},
        "DAA progress must be selected-chain-v1 or blue-score-v1",
    )
    from consensus.pow import validate_compact_target
    try:
        validate_compact_target(daa_initial_bits, pow_limit_bits)
    except ValueError as exc:
        raise NetworkConfigurationError(f"Invalid DAA initial target: {exc}") from exc
    eco_output = consensus.get("eco_fund_output", [])
    _require(
        isinstance(eco_output, list)
        and len(eco_output) == 2
        and all(isinstance(value, str) and _HEX_66.fullmatch(value) for value in eco_output),
        "consensus.eco_fund_output must contain two compressed 33-byte points",
    )
    checkpoints_raw = consensus.get("hard_checkpoints", {})
    _require(isinstance(checkpoints_raw, dict), "consensus.hard_checkpoints must be an object")
    hard_checkpoints: dict[int, str] = {}
    for raw_height, raw_hash in checkpoints_raw.items():
        try:
            height = int(raw_height)
        except (TypeError, ValueError) as exc:
            raise NetworkConfigurationError("Checkpoint heights must be integers") from exc
        _require(height > 0, "Hard checkpoint height must be positive")
        _require(
            isinstance(raw_hash, str) and bool(_HEX_64.fullmatch(raw_hash)),
            f"Hard checkpoint {height} must be a 32-byte hex hash",
        )
        hard_checkpoints[height] = raw_hash.lower()
    if requested_name == "mainnet" and 271_828 in hard_checkpoints:
        _require(
            consensus.get("p2p_protocol_version") == "1.2.0",
            "Checkpointed MAINNET requires paginated P2P protocol 1.2.0",
        )
        _require(
            consensus.get("tokenomics") == {
                "version": "2.4.1",
                "daily_quotas_sha256": "7896cf36303e123f799c71b4d72b74a684e0079c1e32e57eac34d4ed04913e59",
                "combo_stage_boundary_liquidation": True,
                "post_mining_fee_split_percent": {"miner": 50, "combo": 50},
            },
            "Checkpointed MAINNET must bind canonical tokenomics v2.4.1",
        )

    release = raw.get("release", {})
    approved = release.get("approved") is True
    approval_id = release.get("approval_id", "")
    if requested_name == "mainnet" and (not approved or not isinstance(approval_id, str) or not approval_id.strip()):
        raise MainnetReleaseError(
            "BTC-K mainnet is release-locked: an independently reviewed, approved genesis manifest is required"
        )

    genesis = GenesisConfig(
        version=int(genesis_raw["version"]),
        timestamp=float(genesis_raw["timestamp"]),
        bits=int(genesis_raw["bits"]),
        nonce=int(str(genesis_raw["nonce"]), 0),
        headline=str(genesis_raw["headline"]),
        coinbase_output=(bytes.fromhex(outputs[0]), bytes.fromhex(outputs[1])),
        expected_hash=expected_hash.lower(),
    )
    _require(genesis.version > 0, "Genesis version must be positive")
    _require(genesis.timestamp > 0, "Genesis timestamp must be positive")
    _require(genesis.bits > 0, "Genesis bits must be positive")
    _require(0 <= genesis.nonce < 2**64, "Genesis nonce must fit uint64")
    _require(bool(genesis.headline), "Genesis headline must not be empty")

    features = raw.get("features", {})
    _require(isinstance(features, dict), "features must be an object")
    _require(all(isinstance(key, str) and isinstance(value, bool) for key, value in features.items()), "Feature flags must be boolean")
    consensus_identity = {
        "manifest_version": raw["manifest_version"],
        "network": raw["network"],
        "chain_id": chain_id,
        "p2p_magic": p2p_magic.lower(),
        "consensus": consensus,
        "genesis": genesis_raw,
    }
    consensus_fingerprint = hashlib.sha256(
        json.dumps(consensus_identity, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()

    return NetworkConfig(
        manifest_version=1,
        name=requested_name,
        chain_id=chain_id,
        p2p_magic=p2p_magic.lower(),
        data_subdir=data_subdir,
        block_version=block_version,
        pow_limit_bits=pow_limit_bits,
        max_future_seconds=max_future_seconds,
        max_parents=max_parents,
        ghostdag_k=ghostdag_k,
        max_merge_set_size=max_merge_set_size,
        daa_window_blocks=daa_window_blocks,
        daa_initial_bits=daa_initial_bits,
        daa_target_interval_ms=daa_target_interval_ms,
        daa_median_time_blocks=daa_median_time_blocks,
        daa_max_adjustment_factor=daa_max_adjustment_factor,
        daa_progress=daa_progress,
        eco_fund_output=(bytes.fromhex(eco_output[0]), bytes.fromhex(eco_output[1])),
        hard_checkpoints=hard_checkpoints,
        genesis=genesis,
        features=dict(features),
        manifest_path=path.resolve(),
        release_approved=approved,
        release_approval_id=str(approval_id),
        consensus_fingerprint=consensus_fingerprint,
    )


def load_network(name: Optional[str] = None, manifest_path: Optional[Path | str] = None) -> NetworkConfig:
    """Load and validate a network manifest; defaults safely to devnet."""
    selected = (name or os.getenv("BTCK_NETWORK", "devnet")).strip().lower()
    if selected not in SUPPORTED_NETWORKS:
        raise NetworkConfigurationError(
            f"Unsupported BTC-K network {selected!r}; choose one of: {', '.join(sorted(SUPPORTED_NETWORKS))}"
        )

    env_manifest = os.getenv("BTCK_NETWORK_MANIFEST")
    path = Path(manifest_path or env_manifest or (DEFAULT_MANIFEST_DIR / f"{selected}.json"))
    return _parse_manifest(path.expanduser().resolve(), selected)


def require_feature(feature: str, network: Optional[NetworkConfig | str] = None) -> NetworkConfig:
    """Fail closed unless the selected network manifest enables a feature."""
    config = network if isinstance(network, NetworkConfig) else load_network(network)
    if not config.feature_enabled(feature):
        raise DisabledFeatureError(
            f"Feature {feature!r} is disabled on {config.name}; it remains outside the safe deployment path"
        )
    return config
