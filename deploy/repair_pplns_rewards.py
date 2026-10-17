#!/usr/bin/env python3
"""Apply an explicitly approved, hash-bound PPLNS accounting repair atomically.

This script never signs or broadcasts payments. Stop the PPLNS writer first;
its caller must retain a consistent ledger backup and disabled-payment gate.
"""
import argparse
from contextlib import closing
import gzip
import hashlib
import json
from pathlib import Path
import sqlite3
import time


def export_ledger(conn):
    conn.row_factory = sqlite3.Row
    return {
        'shares':[dict(r) for r in conn.execute('SELECT id,wallet_address,difficulty,timestamp,block_hash_settled FROM shares ORDER BY id')],
        'blocks':[dict(r) for r in conn.execute('SELECT rowid AS settlement_order,* FROM blocks ORDER BY rowid')],
        'miners':[dict(r) for r in conn.execute('SELECT * FROM miners')],
        'payouts':[dict(r) for r in conn.execute('SELECT * FROM payouts')],
        'pool_meta':[dict(r) for r in conn.execute('SELECT * FROM pool_meta')],
        'intents':[dict(r) for r in conn.execute('SELECT * FROM payout_intents')],
    }


def fingerprint(ledger):
    return hashlib.sha256(json.dumps(ledger,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def apply_plan(database, plan_path, expected_sha256, approved_fee_adjustment_satk=0):
    blob=Path(plan_path).read_bytes()
    if len(blob)>2*1024*1024 or hashlib.sha256(blob).hexdigest()!=expected_sha256:
        raise ValueError('Accounting plan hash/size mismatch')
    plan=json.loads(gzip.decompress(blob))
    if plan.get('format')!='btck-pplns-reward-repair-v1':
        raise ValueError('Unsupported accounting plan')
    if plan['operator_fee_adjustment_satk']!=approved_fee_adjustment_satk:
        raise ValueError('Explicit approval required for the exact operator-fee adjustment')
    if len(plan['records'])>10000 or len(plan['original']['shares'])>100000:
        raise ValueError('Accounting repair exceeds bounded scope')
    from stratum.pplns import PPLNSEngine
    from stratum.pplns_rewards import allocate_reward
    engine=PPLNSEngine(database)
    marker='reward_repair_'+expected_sha256
    with closing(engine._get_connection()) as conn, conn:
        conn.execute('PRAGMA synchronous=FULL')
        conn.execute('BEGIN IMMEDIATE')
        old=conn.execute('SELECT value FROM pool_meta WHERE key=?',(marker,)).fetchone()
        if old:
            result=json.loads(old[0]);result['already_applied']=True
            return result
        actual=export_ledger(conn)
        if fingerprint(actual)!=plan['source_fingerprint'] or fingerprint(plan['original'])!=plan['source_fingerprint']:
            raise ValueError('Source accounting changed; refuse to overwrite it')
        if actual['payouts'] or actual['intents'] or any(m['paid_satk'] for m in actual['miners']):
            raise ValueError('Paid or pending funds require a separate reconciliation')
        if conn.execute('SELECT COUNT(*) FROM pplns_block_allocations').fetchone()[0]:
            raise ValueError('Existing reward allocations must not be replaced')
        old_balances={m['wallet_address']:m['unpaid_satk'] for m in actual['miners']}
        if old_balances!=plan['original_balances']:
            raise ValueError('Original liabilities disagree')
        originals={b['block_hash']:b for b in actual['blocks']}
        if len(plan['records'])!=len(originals) or len({r['block_hash'] for r in plan['records']})!=len(originals):
            raise ValueError('Accounting plan must cover each block exactly once')
        totals={};gross=fees=0;fee_adjustment=0
        for record in plan['records']:
            block=record['block_hash'];original=originals[block]
            if record['original']!=original or original['mode']!='PPLNS':
                raise ValueError('Original block mismatch')
            credits=record['credits'];amount=record['reward_satk'];fee=record['pool_fee_satk']
            if type(amount) is not int or type(fee) is not int or fee<0 or any(type(v) is not int or v<0 for v in credits.values()):
                raise ValueError('Invalid repaired integer amounts')
            if sum(credits.values())+fee!=amount:
                raise ValueError('Repaired block does not conserve SatK')
            kind=record['allocation_kind']
            if kind=='window':
                expected=allocate_reward(amount,record['weights'],original['finder_wallet'],'.05','.005')
                if (fee,record['finder_bonus_satk'],credits)!=expected:
                    raise ValueError('Repaired allocation differs from the proven window')
            elif kind=='legacy_fixed':
                nominal_fee=allocate_reward(amount,{'proof-placeholder':'1'},original['finder_wallet'],'.05','.005')[0]
                fee_adjustment+=nominal_fee-fee
            else:
                raise ValueError('Unknown allocation proof kind')
            gross+=amount;fees+=fee
            for wallet,credit in credits.items():totals[wallet]=totals.get(wallet,0)+credit
            conn.execute('UPDATE blocks SET reward_satk=?,pool_fee_satk=?,finder_bonus_satk=?,distributed_satk=?,height=? WHERE block_hash=?',
                         (amount,fee,record['finder_bonus_satk'],sum(credits.values()),record['canonical_height'] if record['canonical_height'] is not None else original['height'],block))
            conn.execute('INSERT INTO pplns_block_allocations VALUES(?,?,?,?,?,?,?, ?,?,?)',
                         (block,json.dumps(record['weights'],sort_keys=True),'.05','.005',json.dumps(credits,sort_keys=True),
                          0,0,record['canonical_height'],time.time(),kind))
        if fee_adjustment!=approved_fee_adjustment_satk or totals!=plan['new_balances'] or gross!=fees+sum(totals.values()):
            raise ValueError('Global accounting repair invariant failed')
        if set(totals)!=set(old_balances):
            raise ValueError('Accounting repair must preserve the miner account set')
        for wallet,balance in totals.items():
            changed=conn.execute('UPDATE miners SET unpaid_satk=? WHERE wallet_address=? AND unpaid_satk=? AND paid_satk=0',
                                 (balance,wallet,old_balances[wallet])).rowcount
            if changed!=1:raise ValueError('Concurrent account change')
        conn.execute('DELETE FROM pplns_reward_holds')
        if engine.has_reward_reconciliation_hold(conn):
            raise ValueError('Repaired ledger still has an unresolved reward hold')
        result={'verification':'PASS','at':time.time(),'records':len(plan['records']),
                'old_unpaid_satk':sum(old_balances.values()),'new_unpaid_satk':sum(totals.values()),
                'gross_satk':gross,'fees_satk':fees,'operator_fee_adjustment_satk':fee_adjustment,
                'plan_sha256':expected_sha256,'payments_made':False,'already_applied':False}
        conn.execute('INSERT INTO pool_meta VALUES(?,?)',(marker,json.dumps(result,sort_keys=True)))
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database',type=Path,required=True)
    parser.add_argument('--plan',type=Path,required=True)
    parser.add_argument('--plan-sha256',required=True)
    parser.add_argument('--approve-fee-adjustment-satk',type=int,default=0)
    args=parser.parse_args()
    print(json.dumps(apply_plan(args.database,args.plan,args.plan_sha256,args.approve_fee_adjustment_satk)))

if __name__=='__main__':main()
