"""Exercise the actual browser render functions; never touch production miners."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import unittest
from unittest.mock import patch
import web_explorer_server as explorer

ROOT=Path(__file__).resolve().parents[1]


class PoolOnlineTests(unittest.TestCase):
    def test_public_summary_excludes_offline_without_mutating_history(self):
        pool={'pool_mode':'SOLO','workers':[
            {'worker_name':'online','wallet_address':'test','online':True,'hashrate_hps':0},
            {'worker_name':'offline','wallet_address':'test','online':False,'hashrate_hps':1e12}]}
        with patch.object(explorer,'_CACHED_NODE_INFO',{}),patch.object(explorer,'_CACHED_POOL_STATUS',pool),patch.object(explorer,'_CACHED_PPLNS_STATUS',{}):
            result=explorer.build_production_stats_payload()
        self.assertEqual(len(result['miners_summary']),1)
        self.assertEqual(len(pool['workers']),2)
        self.assertTrue(all(w['status']=='ONLINE' for w in result['miners_summary'].values()))

    def test_browser_counts_disconnect_and_reconnect(self):
        path=Path(os.environ.get('BTCK_POOL_HTML_PATH',ROOT/'web/index.html'))
        html=path.read_text()
        render=html.split('    function renderWorkersTable() {',1)[1].split('    function renderBlocksTable()',1)[0]
        render='function renderWorkersTable() {'+render
        lookup=html.split('    async function executeMinerLookup()',1)[1].split('    function startCountdownTicker',1)[0]
        lookup='async function executeMinerLookup()'+lookup
        node=shutil.which('node') or str(ROOT/'build/release-tools/node-v24.21.0-darwin-arm64/bin/node')
        script='''const vm=require('node:vm'),assert=require('node:assert/strict');
const nodes={};const ctx={currentStats:{miners_summary:{live:{status:'ONLINE',mode:'SOLO'},gone:{status:'OFFLINE',mode:'PPLNS'}}},activeWorkerFilter:'ALL',escapeHtml:String,showToast:()=>{},document:{getElementById:(id)=>nodes[id]??=( {style:{},value:'dummy-test-address'} )},fetch:async()=>({ok:true,json:async()=>({found:true,miner_id:'test-account',active_worker_count:1,worker_count:2,workers:[{worker_name:'live-worker',online:true,mode:'SOLO'},{worker_name:'offline-only-name',online:false,mode:'PPLNS'}]})})};
vm.createContext(ctx);
'''+f'vm.runInContext({json.dumps(render+lookup)},ctx);'+'''
(async()=>{
ctx.renderWorkersTable();assert.equal(nodes['worker-count-all'].innerText,1);assert.equal(nodes['worker-count-pplns'].innerText,0);assert(!nodes['workers-table-tbody'].innerHTML.includes('gone'));
ctx.currentStats.miners_summary.live.status='OFFLINE';ctx.renderWorkersTable();assert.equal(nodes['worker-count-all'].innerText,0);assert(nodes['workers-table-tbody'].innerHTML.includes('No workers matched'));
ctx.currentStats.miners_summary.gone.status='ONLINE';ctx.renderWorkersTable();assert.equal(nodes['worker-count-pplns'].innerText,1);assert(nodes['workers-table-tbody'].innerHTML.includes('gone'));
await ctx.executeMinerLookup();assert(!nodes['mp-worker-list'].innerHTML.includes('offline-only-name'));assert.equal(nodes['mp-workers'].innerText,'1 / 1');
console.log('PASS: online-only list/counts, disconnect, reconnect, lookup');
})().catch(e=>{console.error(e);process.exitCode=1});'''
        result=subprocess.run([node,'-e',script],capture_output=True,text=True,timeout=10)
        self.assertEqual(result.returncode,0,result.stderr)


if __name__=='__main__':
    unittest.main()
