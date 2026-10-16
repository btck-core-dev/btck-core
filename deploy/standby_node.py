#!/usr/bin/env python3
"""Standby-only startup: recover once, seal snapshot offline, then serve locally.

No seed dialing at startup. Operators attach a separately approved paced source
only after recovery and isolated acceptance. Never use this wrapper on Seed-A.
"""
import asyncio
import logging
import os
from pathlib import Path
import signal
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from config.network import load_network
from node.daemon import BTCKDaemon
from p2p.standby_sync import StandbySyncNetwork
from wallet_sync_index import build_wallet_sync_index


def prepare_standby(daemon, *, wallet_index=None, managed=False,
                    health_file=None, managed_peer_file=None):
    if daemon.running or daemon.server is not None or daemon.p2p.running:
        raise RuntimeError('standby snapshot requires a non-serving startup state')
    if daemon.chain_store is None or not daemon.chain_store._record_count:
        raise RuntimeError('standby startup requires a nonempty durable journal')
    if daemon.p2p.seed_peers:
        raise RuntimeError('standby startup must not auto-dial seed peers')
    if daemon.dag.utxo_set.total_value_sats() != daemon.dag.issued_supply_sats:
        raise RuntimeError('startup asset conservation failed')
    logging.info('STANDBY RECOVERY: sealing recovered state before opening listeners')
    root = daemon.dag.state_root()
    sequence, block_hash = daemon.chain_store.seal_latest_checkpoint(root)
    daemon.chain_store.write_snapshot_v2(daemon.dag,sequence,block_hash,root)
    daemon.latest_checkpoint_sequence = sequence
    daemon.latest_checkpoint_state_root = root
    if wallet_index is not None:
        build_wallet_sync_index(daemon.chain_store.snapshot_v2_path,Path(wallet_index))
    policy = dict(p2p_host=daemon.p2p_host,p2p_port=daemon.p2p_port,
                  block_acceptor=daemon.accept_block,
                  requests_per_second=2.0,max_outstanding=32,request_timeout=30.0)
    if managed:
        from p2p.managed_sync import ManagedSyncNetwork
        from p2p.source_health_gate import SourceHealthGate
        if not health_file or not managed_peer_file:
            raise RuntimeError('managed standby requires explicit health and peer files')
        daemon.p2p = ManagedSyncNetwork(daemon.dag,managed_peers=[],
            connection_allowed=SourceHealthGate(health_file),managed_peer_file=managed_peer_file,
            inbound_allowed_hosts=('127.0.0.1','::1'),**policy)
    else:
        daemon.p2p = StandbySyncNetwork(daemon.dag,seed_peers=[],**policy)
    logging.info('STANDBY POLICY LOADED: %s managed=%s peers_configured=%s',
                 type(daemon.p2p).__name__, managed,
                 len(getattr(daemon.p2p,'connection_requests',{})))
    logging.info('STANDBY RECOVERY READY: sequence=%s snapshot=%s wallet_index=%s',
                 sequence,daemon.chain_store.snapshot_v2_path,wallet_index)
    return sequence,root


async def serve(daemon):
    stopped = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGTERM,signal.SIGINT):
        loop.add_signal_handler(signum,stopped.set)
    await daemon.start()
    try:
        await stopped.wait()
    finally:
        await daemon.stop()


def main():
    if os.environ.get('BTCK_STANDBY_STARTUP') != '1':
        raise RuntimeError('explicit BTCK_STANDBY_STARTUP=1 required')
    if os.environ.get('BTCK_SEEDS','').strip():
        raise RuntimeError('standby recovery refuses automatic seed dialing')
    if os.environ.get('BTCK_NETWORK') != 'mainnet':
        raise RuntimeError('deployment wrapper requires mainnet')
    host = os.environ.get('BTCK_RPC_HOST','127.0.0.1')
    if host not in ('127.0.0.1','::1'):
        raise RuntimeError('standby RPC must remain loopback-only')
    manifest = Path(__file__).resolve().parents[1]/'params/networks/mainnet.json'
    daemon = BTCKDaemon(network=load_network('mainnet',str(manifest)),
                        host=host,port=int(os.environ.get('BTCK_RPC_PORT','16110')),
                        p2p_host=os.environ.get('BTCK_P2P_HOST','0.0.0.0'),
                        p2p_port=int(os.environ.get('BTCK_P2P_PORT','16111')),
                        seed_peers=[],data_root=os.environ['BTCK_DATA_ROOT'],
                        persistent=True,
                        rpc_token=os.environ.get('BTCK_RPC_TOKEN'))
    prepare_standby(daemon,wallet_index=os.environ.get('BTCK_STANDBY_WALLET_INDEX'),
        managed=os.environ.get('BTCK_STANDBY_MANAGED')=='1',
        health_file=os.environ.get('BTCK_STANDBY_HEALTH_FILE'),
        managed_peer_file=os.environ.get('BTCK_STANDBY_PEER_FILE'))
    asyncio.run(serve(daemon))


if __name__ == '__main__':
    main()
