#!/usr/bin/env python3
"""Offline protocol/dependency replay, NOT a mainnet consensus/ledger replay.

Uses real public mainnet payloads from an immutable local journal. Inventories
are synthetic (merge blocks in journal order), not a captured incident wire
trace. No sockets, RPC, remote hosts, production writes or node startup.
"""
import argparse
import asyncio
from collections import Counter, deque
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
import time
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from config.network import load_network
from consensus.pow import hash_meets_target
from node.blockdag import BlockDAG, transaction_commitment


class DependencyView:
    """Membership-only prefix. Deliberately does not assert UTXO/DAA validity."""
    def __init__(self, network, known, anchor):
        self.network = network
        self.genesis_hash = BlockDAG(network=network).genesis_hash
        self.blocks = dict.fromkeys(known)
        self.blocks[self.genesis_hash] = None
        self.blocks[anchor] = SimpleNamespace(blue_score=0)
        self.selected_tip = anchor
        self.virtual_selected_parent_chain = [anchor]
        self.admitted = set()

    def add_block(self, block):
        if block.hash in self.blocks:
            return False
        if not all(h in self.blocks for h in block.parents):
            raise ValueError('parent dependency not admitted')
        if block.payload_hash != transaction_commitment(block.transactions):
            raise ValueError('payload commitment mismatch')
        if not hash_meets_target(bytes.fromhex(block.hash), block.bits, self.network.pow_limit_bits):
            raise ValueError('claimed PoW failed')
        # This is NOT full consensus validation: prefix membership supplies no
        # DAA, UTXO, GHOSTDAG or asset-accounting state.
        block.blue_score = len(self.admitted)+1
        self.blocks[block.hash] = block
        self.admitted.add(block.hash)
        self.selected_tip = block.hash
        self.virtual_selected_parent_chain.append(block.hash)
        return True


def load_fixture(path, count):
    if not isinstance(count, int) or not 1 <= count <= 20000:
        raise ValueError('fixture record count must be between 1 and 20000')
    conn = sqlite3.connect('file:'+str(Path(path).resolve())+'?mode=ro', uri=True)
    try:
        end = conn.execute('SELECT MAX(sequence) FROM accepted_blocks').fetchone()[0]
        if end is None:
            raise ValueError('fixture journal is empty')
        start = end-count
        if start < 1:
            raise ValueError('fixture prefix must be nonempty')
        known = {r[0] for r in conn.execute('SELECT block_hash FROM accepted_blocks WHERE sequence<=?', (start,))}
        anchor = conn.execute('SELECT block_hash FROM accepted_blocks WHERE sequence=?', (start,)).fetchone()[0]
        rows = conn.execute('SELECT sequence,block_hash,payload_json FROM accepted_blocks WHERE sequence>? ORDER BY sequence', (start,)).fetchall()
        payloads = {h: json.loads(p) for _,h,p in rows}
        # Deliberately omit merge parents from advertised inventories to exercise
        # native ancestor requests; the real serialized blocks are unchanged.
        inventory = [h for _,h,_ in rows if len(payloads[h]['parents']) > 1]
        if not inventory or inventory[-1] != rows[-1][1]:
            inventory.append(rows[-1][1])
        required, pending = set(), list(inventory)
        while pending:
            h = pending.pop()
            if h in known or h in required:
                continue
            if h not in payloads:
                raise ValueError('fixture does not close over its known prefix')
            required.add(h)
            pending.extend(payloads[h]['parents'])
        return start, end, known, anchor, payloads, inventory, required
    finally:
        conn.close()


async def exercise(module, fixture, *, max_events=50000, gossip=True, max_seconds=180,
                   network_config=None):
    start, end, known, anchor, payloads, inventory, required = fixture
    dag = DependencyView(network_config or load_network('mainnet'), known, anchor)
    network = module.P2PNetwork(dag)
    wire = deque()
    counts = Counter()
    requested = Counter()
    inflight = Counter()
    page_position = {h:i for i,h in enumerate(inventory)}
    peak_orphans = peak_outstanding = peak_wire = peak_requests = 0
    class Writer:
        def close(self): pass
        def is_closing(self): return False
    peer = module.PeerConnection(None, Writer(), 'offline-fixture:1', network.network_magic)
    peer.handshake_complete = True
    network.peers[peer.peer_addr] = peer
    async def send(kind, payload):
        nonlocal peak_requests
        counts[kind] += 1
        if kind == 'GET_BLOCK':
            h = payload['hash']
            requested[h] += 1
            inflight[h] += 1
            peak_requests = max(peak_requests, sum(inflight.values()))
            if h not in payloads:
                raise ValueError('unexpected request outside fixture: '+h)
            wire.append(('BLOCK', payloads[h]))
        elif kind == 'GET_BLOCKS':
            position = 0
            for h in reversed(payload.get('locator', [])):
                if h in page_position:
                    position = page_position[h]+1
                    break
            batch = inventory[position:position+500]
            wire.append(('INV', {'blocks':batch, 'more':position+len(batch)<len(inventory)}))
            if gossip:
                wire.append(('INV', {'blocks':[inventory[-1]]}))
    peer.send_msg = send
    await send('GET_BLOCKS', {'locator':[anchor]})
    started = last_report = time.monotonic()
    events = 0
    error = None
    while wire and events < max_events:
        if time.monotonic()-started > max_seconds:
            error = 'time budget exceeded'
            break
        kind, payload = wire.popleft()
        if kind == 'BLOCK':
            inflight[payload['hash']] -= 1
            if not inflight[payload['hash']]:
                del inflight[payload['hash']]
        try:
            await network._process_msg(peer, {'type':kind, 'payload':payload})
        except Exception as exc:
            error = type(exc).__name__+': '+str(exc)
            break
        events += 1
        peak_orphans = max(peak_orphans, len(network.orphan_pool))
        peak_outstanding = max(peak_outstanding, len(getattr(peer, 'requested_blocks', ())))
        peak_wire = max(peak_wire, len(wire))
        if time.monotonic()-last_report >= 10:
            print(json.dumps({'event':'offline_progress', 'implementation':module.__name__,
                              'admitted':len(dag.admitted), 'required':len(required),
                              'events':events, 'orphans':len(network.orphan_pool)}), flush=True)
            last_report = time.monotonic()
        await asyncio.sleep(0)
    return {'implementation':module.__name__, 'scope':'protocol_dependencies_only_not_full_consensus',
            'inventory':'synthetic_merge_inventory_not_captured_incident',
            'prefix_sequence':start, 'end_sequence':end,
            'inventory_hashes':len(inventory), 'required_blocks':len(required),
            'admitted':len(dag.admitted), 'missing':len(required-dag.admitted),
            'requests':sum(requested.values()), 'duplicate_requests':sum(n-1 for n in requested.values()),
            'events':events, 'wire_queue_remaining':len(wire), 'page_requests':counts['GET_BLOCKS'],
            'peak_orphans':peak_orphans, 'peak_outstanding':peak_outstanding,
            'peak_simulated_wire_queue':peak_wire, 'peak_request_responses_queued':peak_requests,
            'most_repeated_requests':requested.most_common(3),
            'pending_orphans':len(network.orphan_pool), 'error':error,
            'elapsed_seconds':round(time.monotonic()-started,3),
            'sync_counters':dict(getattr(network, 'sync_counters', {}))}


async def main(args):
    fixture = load_fixture(args.journal, args.records)
    paths = [('baseline', Path(__file__).resolve().parents[1]/'tests/fixtures/p2p_network_seedb_20261015.py'),
             ('candidate', Path(__file__).resolve().parents[1]/'src/p2p/p2p_network.py')]
    for label, path in paths:
        if args.implementation not in ('both',label):
            continue
        spec = importlib.util.spec_from_file_location('offline_'+label, path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        result = await exercise(module, fixture, max_events=args.max_events,
                                gossip=not args.no_gossip, max_seconds=args.max_seconds)
        print(json.dumps(result, sort_keys=True), flush=True)
        if label == 'candidate' and (result['missing'] or result['error']):
            raise SystemExit(1)


if __name__ == '__main__':
    import logging
    logging.basicConfig(level=logging.WARNING)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--journal', required=True)
    parser.add_argument('--records', type=int, default=5400)
    parser.add_argument('--implementation', choices=('both','baseline','candidate'), default='both')
    parser.add_argument('--max-events', type=int, default=50000)
    parser.add_argument('--max-seconds', type=float, default=180)
    parser.add_argument('--no-gossip', action='store_true')
    asyncio.run(main(parser.parse_args()))
