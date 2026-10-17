"""No production RPC: preserve actual failure signals through cached telemetry."""
import asyncio
import json
import unittest
from unittest.mock import AsyncMock, patch

from node.rpc_client import HTTPNodeRPCClient, RPCError
from stratum.stratum_server import BTCKStratumServer
import web_explorer_server as explorer
from deploy.seed_b_source_guard import rpc_health_reason


class RPCHealthTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_http_submission_timeout_survives_public_cache_passthrough(self):
        requests=[]
        async def handler(reader,writer):
            try:
                headers=await reader.readuntil(b'\r\n\r\n')
                length=next(int(line.split(b':',1)[1]) for line in headers.split(b'\r\n')
                            if line.lower().startswith(b'content-length:'))
                request=json.loads(await reader.readexactly(length))
                requests.append(request['method'])
                if request['method']=='submitBlock' and requests.count('submitBlock')==1:
                    # Wait for client timeout/cancellation to close the socket.
                    await reader.read()
                    return
                body=json.dumps({'jsonrpc':'2.0','id':request['id'],'result':{}}).encode()
                writer.write(b'HTTP/1.1 200 OK\r\nContent-Length: '+str(len(body)).encode()+
                             b'\r\nConnection: close\r\n\r\n'+body)
                await writer.drain()
            finally:
                writer.close()
                await writer.wait_closed()
        server=await asyncio.start_server(handler,'127.0.0.1',0)
        try:
            client=HTTPNodeRPCClient('http://127.0.0.1:'+str(server.sockets[0].getsockname()[1])+'/rpc')
            with self.assertRaises(RPCError):
                await client.call('submitBlock',{'block':'disposable-private-params'},timeout=.1)
            for method in ('getBlockTemplate','getDashboardInfo','submitBlock'):
                await client.call(method)
            pool=BTCKStratumServer(network='simnet',node_rpc=client).get_status_payload()
            with patch.object(explorer,'_CACHED_NODE_INFO',{}),patch.object(explorer,'_CACHED_POOL_STATUS',pool),patch.object(explorer,'_CACHED_PPLNS_STATUS',{}):
                public=explorer.build_production_stats_payload()
            health=public['node_rpc_health']
            self.assertIn('submitBlock',rpc_health_reason(health,now=health['observed_at']))
            self.assertEqual(health['methods']['submitBlock']['failures'],1)
            self.assertEqual(health['methods']['submitBlock']['successes'],1)
            self.assertEqual(len(requests),4,'status/cache passthrough must not issue another RPC')
            self.assertNotIn('disposable-private-params',json.dumps(health))
        finally:
            server.close()
            await server.wait_closed()

    async def test_real_timeout_record_survives_subsequent_success(self):
        client=HTTPNodeRPCClient(token='disposable-test-token')
        async def stalled(*args):
            await __import__('asyncio').sleep(1)
        with patch.object(client,'_call',AsyncMock(side_effect=stalled)):
            with self.assertRaises(RPCError):
                await client.call('getBlockTemplate',timeout=.1)
        with patch.object(client,'_call',AsyncMock(return_value={'public':'result'})):
            await client.call('getBlockTemplate')
            await client.call('getDashboardInfo')
        health=client.health_snapshot();row=health['methods']['getBlockTemplate']
        self.assertEqual(row['successes'],1)
        self.assertEqual(row['failures'],1)
        self.assertEqual(row['consecutive_failures'],0)
        self.assertIsNotNone(row['last_failure_at'])
        self.assertIn('recovery window',rpc_health_reason(health,now=health['observed_at']))
        self.assertNotIn('disposable-test-token',json.dumps(health))

    async def test_error_payload_not_leaked_and_unrelated_methods_not_recorded(self):
        client=HTTPNodeRPCClient()
        with patch.object(client,'_call',AsyncMock(side_effect=RPCError('private-message-not-telemetry'))):
            with self.assertRaises(RPCError): await client.call('getDashboardInfo')
        with patch.object(client,'_call',AsyncMock(return_value='private-response')):
            await client.call('submitTransaction',{'private-params':'not-telemetry'})
        text=json.dumps(client.health_snapshot())
        for private in ('private-message','private-response','private-params','submitTransaction'):
            self.assertNotIn(private,text)
        self.assertEqual(len(client.health_snapshot()['methods']),1)

    async def test_submission_tracks_failure_not_block_or_rpc_error_contents(self):
        client=HTTPNodeRPCClient()
        with patch.object(client,'_call',AsyncMock(side_effect=RPCError('private-block-body'))):
            with self.assertRaises(RPCError):
                await client.call('submitBlock',{'block':'private-block-parameters'})
        snapshot=client.health_snapshot()
        self.assertEqual(snapshot['methods']['submitBlock']['failures'],1)
        self.assertNotIn('private-block',json.dumps(snapshot))

    async def test_pool_and_public_cache_forward_health_without_adding_rpc_calls(self):
        client=HTTPNodeRPCClient()
        with patch.object(client,'_call',AsyncMock(return_value={})) as call:
            await client.call('getBlockTemplate');await client.call('getDashboardInfo')
            pool=BTCKStratumServer(network='simnet',node_rpc=client).get_status_payload()
            self.assertEqual(call.await_count,2)
        with patch.object(explorer,'_CACHED_NODE_INFO',{}),patch.object(explorer,'_CACHED_POOL_STATUS',pool),patch.object(explorer,'_CACHED_PPLNS_STATUS',{}):
            public=explorer.build_production_stats_payload()
        self.assertEqual(public['node_rpc_health'],pool['node_rpc_health'])
        self.assertIsNone(rpc_health_reason(public['node_rpc_health'],now=public['node_rpc_health']['observed_at']))
        pool['node_rpc_health']['methods']['getBlockTemplate']['failures']=999
        self.assertEqual(client.health_snapshot()['methods']['getBlockTemplate']['failures'],0)
