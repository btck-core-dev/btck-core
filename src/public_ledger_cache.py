"""Shared public ledger pages only. Never accepts wallet objects or keys."""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import sqlite3


class PublicLedgerCache:
    def __init__(self,path,endpoint,network):
        self.path=Path(path).expanduser()
        self.path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        self.network=dict(network)
        self.bucket=hashlib.sha256((endpoint+json.dumps(network,sort_keys=True)).encode()).hexdigest()
        with self._connect() as conn:
            conn.execute('CREATE TABLE IF NOT EXISTS pages(bucket TEXT,start INTEGER,lim INTEGER,anchor TEXT,payload TEXT,PRIMARY KEY(bucket,start,lim,anchor))')
        os.chmod(self.path,0o600)

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self.path, timeout=10)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def clear(self):
        with self._connect() as conn:
            conn.execute('DELETE FROM pages WHERE bucket=?',(self.bucket,))

    def discard_from(self, height):
        """Invalidate only pages intersecting a reorganized suffix."""
        with self._connect() as conn:
            conn.execute('DELETE FROM pages WHERE bucket=? AND start+lim>?',
                         (self.bucket,int(height)))

    def put(self,page,limit,anchor):
        if page.get('unspent_only'):
            return
        if page.get('network')!=self.network:
            raise ValueError('shared ledger network mismatch')
        start=int(page['from_height']);end=int(page['next_height'])
        blocks=page['blocks']
        if not blocks or len(blocks)!=end-start or not 0<len(blocks)<=limit:
            return
        sanitized=[]
        output_fields=('txid','vout','amount_satk','R_ephem_hex','P_onetime_hex','height',
                       'is_coinbase','timelock_until_height','kind')
        for i,block in enumerate(blocks):
            if int(block['height'])!=start+i or len(bytes.fromhex(block['hash']))!=32:
                raise ValueError('invalid shared ledger block sequence')
            sanitized.append({'height':start+i,'hash':block['hash'],
                'spent':[{k:item[k] for k in ('txid','vout')} for item in block.get('spent',[])],
                'created':[{k:item[k] for k in output_fields if k in item} for item in block.get('created',[])]})
        if page['anchor_hash']!=sanitized[-1]['hash']:
            raise ValueError('invalid shared ledger end anchor')
        public={'format':'btck-wallet-sync-v1','network':self.network,'from_height':start,
                'next_height':end,'anchor_hash':page['anchor_hash'],'blocks':sanitized}
        with self._connect() as conn:
            conn.execute('INSERT OR REPLACE INTO pages VALUES(?,?,?,?,?)',
                         (self.bucket,start,limit,anchor,json.dumps(public,separators=(',',':'))))

    def get(self,start,limit,anchor,tip):
        if tip.get('network')!=self.network:
            raise ValueError('shared ledger live-tip network mismatch')
        with self._connect() as conn:
            row=conn.execute('SELECT payload FROM pages WHERE bucket=? AND start=? AND lim=? AND anchor=?',
                             (self.bucket,start,limit,anchor)).fetchone()
        if not row:
            return None
        page=json.loads(row[0]);end=int(page['next_height']);height=int(tip['tip_height'])
        if end>height+1 or (end==height+1 and page['anchor_hash']!=tip['tip_hash']):
            return None
        # Never reuse terminal/stale metadata from the earlier download.
        return {**page,'tip_height':height,'tip_hash':tip['tip_hash'],
                'done':end==height+1,'source':'shared-local-ledger','unspent_only':False}
